"""Bounded NetworkManager Wi-Fi operations for a worker thread.

Only wireless devices and their profiles are selected. Passwords are supplied via
a short-lived mode-0600 ``passwd-file``, never command arguments. No operation
stops NetworkManager, disables networking, or changes an Ethernet profile.

See the official nmcli ``connection up ... passwd-file`` documentation:
https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html
"""

import math
import os
import re
import subprocess
import tempfile
import time
import uuid as uuid_module


_INTERFACE = re.compile(r"[A-Za-z0-9_.-]{1,15}\Z")
_BSSID = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\Z")
_UUID = re.compile(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\Z")
_UUID_IN_TEXT = re.compile(r"\b[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\b")
_WIFI_TYPES = frozenset(("wifi", "802-11-wireless"))


def split_terse(line):
    """Split nmcli -t --escape yes output without losing SSID punctuation.

    nmcli escapes colons and backslashes. An unknown escape is preserved so
    SSIDs with literal backslashes also survive older or imperfect fixtures.
    """
    fields, field, index = [], [], 0
    while index < len(line):
        value = line[index]
        if value == "\\" and index + 1 < len(line) and line[index + 1] in ":\\":
            field.append(line[index + 1])
            index += 2
            continue
        if value == ":":
            fields.append("".join(field))
            field = []
        else:
            field.append(value)
        index += 1
    fields.append("".join(field))
    return fields


def _text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")


def _control(value):
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _security(value):
    value = " ".join(value.upper().split())
    if value in ("", "--", "NONE", "OPEN"):
        return "open"
    if any(marker in value for marker in ("802.1X", "EAP", "ENTERPRISE", "WEP")):
        return "unsupported"
    if "WPA3" in value or "SAE" in value:
        return "wpa-psk" if "WPA2" in value or "WPA1" in value else "sae"
    if "WPA" in value or "PSK" in value:
        return "wpa-psk"
    return "unsupported"


class _CommandError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class WifiManager:
    """A synchronous backend; invoke methods from the application's worker.

    ``runner`` has the subprocess.run signature and is injectable for offline
    tests. Results contain only JSON-compatible values. Reads are unprivileged;
    profile creation/activation use ``sudo -n nmcli`` unless already root or
    ``use_sudo=False`` is explicitly supplied.
    """

    def __init__(self, runner=None, command_timeout=3, inventory_timeout=6,
                 connect_timeout=35, scan_timeout=12, use_sudo=True,
                 nmcli="nmcli", scratch=None, clock=None):
        self.runner = runner or subprocess.run
        self.command_timeout = self._timeout(command_timeout)
        self.inventory_timeout = self._timeout(inventory_timeout)
        self.connect_timeout = self._timeout(connect_timeout)
        self.scan_timeout = self._timeout(scan_timeout)
        self.use_sudo = bool(use_sudo)
        self.nmcli = str(nmcli)
        self.scratch = scratch
        self.clock = clock or time.monotonic
        self._profiles = {}

    @staticmethod
    def _timeout(value):
        value = float(value)
        if not math.isfinite(value) or not 0 < value <= 120:
            raise ValueError("Timeout must be between zero and 120 seconds")
        return value

    def _run(self, args, *, privileged=False, timeout=None, deadline=None, secret=""):
        timeout = self.command_timeout if timeout is None else timeout
        if deadline is not None:
            timeout = min(timeout, deadline - self.clock())
        if timeout <= 0:
            raise _CommandError("timeout", "Wi-Fi operation timed out. Please retry.")
        root = getattr(os, "geteuid", lambda: -1)() == 0
        prefix = ["sudo", "-n"] if privileged and self.use_sudo and not root else []
        argv = prefix + [self.nmcli, "--colors", "no", "--wait", str(max(1, int(timeout)))] + list(args)
        environment = os.environ.copy()
        environment.update(LC_ALL="C.UTF-8", LANG="C.UTF-8")
        try:
            result = self.runner(argv, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=timeout, check=False,
                                 stdin=subprocess.DEVNULL, shell=False, env=environment)
        except subprocess.TimeoutExpired:
            # Do not return partial output: even a misbehaving command must not
            # accidentally copy a credential into the UI or diagnostic report.
            raise _CommandError("timeout", "Wi-Fi operation timed out. Please retry.") from None
        except FileNotFoundError:
            message = "NetworkManager (nmcli) is not installed."
            if prefix:
                message = "Wi-Fi control needs nmcli and non-interactive sudo permission."
            raise _CommandError("unavailable", message) from None
        except OSError:
            raise _CommandError("unavailable", "Wi-Fi control could not start.") from None
        if result.returncode:
            message = _text(result.stderr or result.stdout).strip()
            if secret:
                message = message.replace(secret, "[redacted]")
            message = message[:600] or "NetworkManager could not complete the Wi-Fi operation."
            lower = message.lower()
            code = "failed"
            if any(word in lower for word in ("not authorized", "permission denied", "password is required", "sudo:")):
                code = "permission"
            elif "not running" in lower:
                code = "unavailable"
            raise _CommandError(code, message)
        return _text(result.stdout)[:262144]

    @staticmethod
    def _base():
        return {"ok": False, "available": False, "interfaces": [], "active": None,
                "saved": [], "radio_enabled": None, "error": "", "error_code": "", "warnings": []}

    @staticmethod
    def _failure(report, error):
        report.update(ok=False, error=str(error), error_code=getattr(error, "code", "invalid-input"))
        return report

    def inventory(self):
        """Report Wi-Fi adapters, current IPv4 address and saved Wi-Fi profiles."""
        report = self._base()
        deadline = self.clock() + self.inventory_timeout
        try:
            output = self._run(["-t", "--escape", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"], deadline=deadline)
        except _CommandError as error:
            return self._failure(report, error)
        for line in output.splitlines()[:64]:
            fields = split_terse(line)
            if len(fields) != 4 or fields[1] not in _WIFI_TYPES or not _INTERFACE.fullmatch(fields[0]):
                continue
            name, _, state, connection = fields
            connected = state == "connected" or state.startswith("connected (")
            report["interfaces"].append({"name": name, "state": state, "connection": "" if connection == "--" else connection,
                                         "uuid": "", "address": "", "ssid": "", "connected": connected})
        report["interfaces"].sort(key=lambda item: (not item["connected"], item["name"]))
        if not report["interfaces"]:
            return self._failure(report, _CommandError("no-adapter", "No Wi-Fi adapter was found. Attach a supported USB Wi-Fi adapter, then retry."))
        report.update(ok=True, available=True)
        try:
            radio = self._run(["-t", "--escape", "yes", "-f", "WIFI", "general"], deadline=deadline).strip()
            value = split_terse(radio)[-1]
            report["radio_enabled"] = True if value == "enabled" else False if value == "disabled" else None
        except _CommandError as error:
            report["warnings"].append(str(error))
        try:
            output = self._run(["-t", "--escape", "yes", "-f", "UUID,TYPE,NAME,DEVICE", "connection", "show"], deadline=deadline)
            profiles = []
            for line in output.splitlines()[:512]:
                fields = split_terse(line)
                if len(fields) == 4 and fields[1] in _WIFI_TYPES and _UUID.fullmatch(fields[0]):
                    identifier, _, name, device = fields
                    profiles.append({"uuid": identifier, "name": name, "device": "" if device == "--" else device})
            profiles.sort(key=lambda item: (not bool(item["device"]), item["name"]))
            live_ids = {item["uuid"] for item in profiles}
            self._profiles = {key: value for key, value in self._profiles.items() if key in live_ids}
            for profile in profiles[:64]:
                cached = self._profiles.get(profile["uuid"])
                if cached is None:
                    try:
                        values = self._run(["-g", "802-11-wireless.ssid,802-11-wireless-security.key-mgmt,connection.autoconnect,connection.interface-name",
                                            "connection", "show", "uuid", profile["uuid"]], deadline=deadline).splitlines()
                        if len(values) < 4:
                            values += [""] * (4 - len(values))
                        values = [split_terse(line)[0] for line in values[:4]]
                        cached = {"ssid": values[0], "key_mgmt": values[1], "autoconnect": values[2] in ("yes", "true", "1"), "interface": values[3]}
                        self._profiles[profile["uuid"]] = cached
                    except _CommandError as error:
                        report["warnings"].append(str(error))
                        if error.code == "timeout":
                            break
                        continue
                record = {**profile, **cached}
                report["saved"].append(record)
                for device in report["interfaces"]:
                    if record["device"] == device["name"] and device["connected"]:
                        device.update(uuid=record["uuid"], ssid=record["ssid"])
        except _CommandError as error:
            report["warnings"].append(str(error))
        for device in report["interfaces"]:
            if not device["connected"]:
                continue
            try:
                output = self._run(["-t", "--escape", "yes", "-f", "GENERAL.CON-UUID,IP4.ADDRESS", "device", "show", device["name"]], deadline=deadline)
                for line in output.splitlines():
                    fields = split_terse(line)
                    if len(fields) == 2 and fields[0] == "GENERAL.CON-UUID" and _UUID.fullmatch(fields[1]):
                        device["uuid"] = fields[1]
                    elif len(fields) == 2 and fields[0].startswith("IP4.ADDRESS") and not device["address"]:
                        device["address"] = fields[1].split("/", 1)[0]
            except _CommandError as error:
                report["warnings"].append(str(error))
            if report["active"] is None:
                report["active"] = dict(device)
        if report["radio_enabled"] is False:
            report["warnings"].append("Wi-Fi radio is disabled. Enable it in the system network settings, then retry.")
        return report

    def _enable_radio(self, report):
        if not report["available"] or report["radio_enabled"] is not False:
            return report
        try:
            self._run(["radio", "wifi", "on"], privileged=True)
        except _CommandError as error:
            return self._failure(report, error)
        refreshed = self.inventory()
        if refreshed["radio_enabled"] is False:
            return self._failure(refreshed, _CommandError("radio-blocked", "Wi-Fi is blocked by the adapter or system. Check its physical switch and USB connection, then retry."))
        return refreshed

    def enable_radio(self):
        """Enable only the Wi-Fi radio for an explicit user operation."""
        return self._enable_radio(self.inventory())

    def scan(self, interface=None):
        """Refresh visible networks on one verified Wi-Fi adapter."""
        report = self.inventory()
        report["networks"] = []
        if not report["available"]:
            return report
        interfaces = {item["name"] for item in report["interfaces"]}
        interface = interface or report["interfaces"][0]["name"]
        if interface not in interfaces:
            return self._failure(report, ValueError("Choose an available Wi-Fi adapter."))
        if report["radio_enabled"] is False:
            report = self._enable_radio(report)
            report["networks"] = []
            if not report["ok"]:
                return report
        try:
            output = self._run(["-t", "--escape", "yes", "-f", "IN-USE,SSID,BSSID,SIGNAL,SECURITY", "device", "wifi", "list",
                                "ifname", interface, "--rescan", "yes"], timeout=self.scan_timeout)
        except _CommandError as error:
            return self._failure(report, error)
        networks = {}
        for line in output.splitlines()[:2048]:
            fields = split_terse(line)
            if len(fields) != 5:
                continue
            active, ssid, bssid, signal, security = fields
            if not ssid or _control(ssid) or len(ssid.encode("utf-8")) > 32 or not _BSSID.fullmatch(bssid):
                continue
            try:
                signal = max(0, min(100, int(signal)))
            except ValueError:
                continue
            kind = _security(security)
            saved = self._matching_profile(report["saved"], ssid, interface, kind)
            network = {"ssid": ssid, "bssid": bssid.upper(), "signal": signal, "security": security or "--",
                       "interface": interface, "connected": active == "*", "saved": saved is not None,
                       "uuid": saved["uuid"] if saved else "", "connectable": kind != "unsupported"}
            key = (ssid, " ".join(security.upper().split()))
            previous = networks.get(key)
            if previous is None or (network["connected"], signal) > (previous["connected"], previous["signal"]):
                networks[key] = network
        report["networks"] = sorted(networks.values(), key=lambda item: (not item["connected"], -item["signal"], item["ssid"]))
        report.update(ok=True, error="", error_code="")
        return report

    @staticmethod
    def _matching_profile(profiles, ssid, interface, kind):
        accepted = {"": "open", "none": "unsupported", "wpa-psk": "wpa-psk", "sae": "sae"}
        matches = [item for item in profiles if item["ssid"] == ssid and item["interface"] in ("", interface)
                   and accepted.get(item["key_mgmt"], "unsupported") == kind]
        matches.sort(key=lambda item: (item.get("device") != interface, not item["autoconnect"], item["uuid"]))
        return matches[0] if matches else None

    @staticmethod
    def _validate(network, password):
        if not isinstance(network, dict):
            raise ValueError("Choose a scanned Wi-Fi network.")
        ssid = network.get("ssid")
        if not isinstance(ssid, str) or not ssid or _control(ssid) or len(ssid.encode("utf-8")) > 32:
            raise ValueError("Wi-Fi name must contain 1 to 32 UTF-8 bytes and no control characters.")
        interface = network.get("interface", "")
        if not isinstance(interface, str) or interface and not _INTERFACE.fullmatch(interface):
            raise ValueError("Invalid Wi-Fi adapter name.")
        bssid = network.get("bssid", "")
        if not isinstance(bssid, str) or bssid and not _BSSID.fullmatch(bssid):
            raise ValueError("Invalid Wi-Fi access point address.")
        security = network.get("security", "--")
        if not isinstance(security, str) or len(security) > 128:
            raise ValueError("Invalid Wi-Fi security type.")
        if not isinstance(network.get("hidden", False), bool):
            raise ValueError("Hidden Wi-Fi setting must be true or false.")
        kind = _security(security)
        if kind == "unsupported":
            raise ValueError("This network needs enterprise or legacy authentication. Configure it in the system network settings.")
        if not isinstance(password, str) or _control(password) or len(password.encode("utf-8")) > 1024:
            raise ValueError("Password contains unsupported characters or is too long.")
        if kind == "open" and password:
            raise ValueError("This open network does not require a password.")
        if password and kind == "wpa-psk":
            hexadecimal = len(password) == 64 and re.fullmatch(r"[0-9A-Fa-f]{64}", password)
            if not hexadecimal and (not password.isascii() or not 8 <= len(password) <= 63):
                raise ValueError("WPA password must contain 8 to 63 ASCII characters, or 64 hexadecimal digits.")
        return ssid, interface, bssid, kind

    def _activate(self, identifier, interface, bssid, password):
        args = ["connection", "up", "uuid", identifier, "ifname", interface]
        if bssid:
            args += ["ap", bssid]
        if not password:
            self._run(args, privileged=True, timeout=self.connect_timeout)
            return
        descriptor, path = tempfile.mkstemp(prefix="ink-wifi-", suffix=".secrets", dir=self.scratch)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                descriptor = None
                stream.write("802-11-wireless-security.psk:" + password + "\n")
            self._run(args + ["passwd-file", path], privileged=True, timeout=self.connect_timeout, secret=password)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def connect(self, network, password=""):
        """Connect a scanned open/personal network and enable saved autoconnect.

        A supplied password gets a fresh profile: this preserves an existing
        saved credential if the new attempt fails. Without a password, a saved
        matching profile is activated directly. Caller-provided UUIDs are never
        trusted; every target is resolved from NetworkManager's Wi-Fi inventory.
        """
        report = self._base()
        try:
            ssid, interface, bssid, kind = self._validate(network, password)
        except (ValueError, UnicodeError) as error:
            return self._failure(report, error)
        report = self.inventory()
        if not report["available"]:
            return report
        names = {item["name"] for item in report["interfaces"]}
        interface = interface or report["interfaces"][0]["name"]
        if interface not in names:
            return self._failure(report, ValueError("Choose an available Wi-Fi adapter."))
        if report["radio_enabled"] is False:
            report = self._enable_radio(report)
            if not report["ok"]:
                return report
        saved = self._matching_profile(report["saved"], ssid, interface, kind)
        if kind != "open" and not password and saved is None:
            return self._failure(report, ValueError("Enter the Wi-Fi password for this network."))
        identifier = saved["uuid"] if saved and not password else str(uuid_module.uuid4())
        created = not (saved and not password)
        added = False
        try:
            if created:
                profile_name = "InkSuite Wi-Fi " + identifier[:8]
                args = ["connection", "add", "type", "wifi", "ifname", interface, "con-name", profile_name,
                        "ssid", ssid, "connection.autoconnect", "no",
                        "connection.autoconnect-priority", "10", "ipv4.method", "auto", "ipv6.method", "auto"]
                if network.get("hidden", False):
                    args += ["802-11-wireless.hidden", "yes"]
                if kind != "open":
                    args += ["802-11-wireless-security.key-mgmt", kind, "802-11-wireless-security.psk-flags", "0"]
                    if kind == "sae":
                        args += ["802-11-wireless-security.pmf", "3"]
                output = self._run(args, privileged=True)
                # NM 1.36 generates the immutable UUID before applying add
                # properties, so connection.uuid cannot be supplied here.
                match = _UUID_IN_TEXT.search(output)
                if match is not None:
                    identifier = match.group(0)
                else:
                    resolved = self._run(["-g", "connection.uuid", "connection", "show", "id", profile_name]).strip()
                    if not _UUID.fullmatch(resolved):
                        raise _CommandError("failed", "Could not identify the new Wi-Fi profile.")
                    identifier = resolved
                added = True
            self._activate(identifier, interface, bssid, password)
        except (_CommandError, OSError) as error:
            if added:
                try:
                    self._run(["connection", "delete", "uuid", identifier], privileged=True)
                except _CommandError as cleanup_error:
                    report["warnings"].append("Could not remove the unsuccessful Wi-Fi profile: " + str(cleanup_error))
            if not isinstance(error, _CommandError):
                error = _CommandError("failed", "Could not prepare the protected Wi-Fi credential file.")
            return self._failure(report, error)
        autoconnect = True
        try:
            self._run(["connection", "modify", "uuid", identifier, "connection.autoconnect", "yes"], privileged=True)
        except _CommandError as error:
            autoconnect = False
            report["warnings"].append("Connected, but automatic reconnect could not be saved: " + str(error))
        self._profiles.pop(identifier, None)
        refreshed = self.inventory()
        refreshed["warnings"] = report["warnings"] + refreshed["warnings"]
        refreshed.update(ok=True, available=True, error="", error_code="", connection_uuid=identifier,
                         autoconnect=autoconnect, network={"ssid": ssid, "interface": interface, "bssid": bssid, "security": network.get("security", "--")},
                         message="Connected to " + ssid + (". Automatic reconnect is enabled." if autoconnect else ". Automatic reconnect could not be saved."))
        return refreshed

    def retry(self):
        """Re-enumerate adapters and rescan; does not reset any network service."""
        return self.scan()
