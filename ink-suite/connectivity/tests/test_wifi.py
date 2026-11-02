import json
import os
from pathlib import Path
import stat
import subprocess
import unittest
import uuid
from unittest import mock

from ..wifi import WifiManager, split_terse


SAVED_UUID = "11111111-2222-3333-4444-555555555555"
WIRED_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def escaped(value):
    return str(value).replace("\\", "\\\\").replace(":", "\\:")


def row(*fields):
    return ":".join(escaped(value) for value in fields)


class FakeNetworkManager:
    """Small stateful nmcli fixture; never invokes a host networking command."""

    def __init__(self):
        self.calls = []
        self.profiles = {}
        self.active = ""
        self.radio = "enabled"
        self.adapter = True
        self.networks = [row("", "Home", "00:11:22:33:44:55", 80, "WPA2")]
        self.credentials = []
        self.fail = None
        self.on_call = None

    def saved(self, ssid="Home", kind="wpa-psk", auto=False, interface="wlan0"):
        self.profiles[SAVED_UUID] = {"ssid": ssid, "kind": kind, "auto": auto, "interface": interface, "name": "Profile name differs from SSID"}
        return self

    @staticmethod
    def command(argv):
        index = argv.index("nmcli")
        return argv[index + 5:]

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        args = self.command(argv)
        if self.on_call:
            self.on_call()
        if self.fail:
            failure = self.fail(args, kwargs)
            if failure is not None:
                return failure
        if args[-2:] == ["device", "status"]:
            output = row("eth0", "ethernet", "connected", "Board LAN") + "\n"
            if self.adapter:
                output += row("wlan0", "wifi", "connected" if self.active else "disconnected", "Wi-Fi profile" if self.active else "--") + "\n"
                output += row("p2p-dev-wlan0", "wifi-p2p", "disconnected", "--") + "\n"
        elif args[-1:] == ["general"]:
            output = self.radio + "\n"
        elif args[-2:] == ["connection", "show"]:
            output = row(WIRED_UUID, "802-3-ethernet", "Board LAN", "eth0") + "\n"
            output += "".join(row(identifier, "802-11-wireless", value["name"], "wlan0" if self.active == identifier else "--") + "\n"
                              for identifier, value in self.profiles.items())
        elif args[:1] == ["-g"]:
            value = self.profiles[args[-1]]
            output = "\n".join(escaped(item) for item in (value["ssid"], value["kind"], "yes" if value["auto"] else "no", value["interface"])) + "\n"
        elif "device" in args and "show" in args:
            output = row("GENERAL.CON-UUID", self.active) + "\n" + row("IP4.ADDRESS[1]", "192.168.7.24/24") + "\n"
        elif "wifi" in args and "list" in args:
            output = "\n".join(self.networks) + "\n"
        elif args[:2] == ["connection", "add"]:
            # Mirror NM 1.36's immutable generated UUID. Supplying it as an
            # add property would fail on the actual board.
            if "connection.uuid" in args:
                return subprocess.CompletedProcess(argv, 2, "", "Error: failed to modify connection.uuid: the property can't be changed.")
            identifier = str(uuid.uuid4())
            self.profiles[identifier] = {"ssid": args[args.index("ssid") + 1], "kind": args[args.index("802-11-wireless-security.key-mgmt") + 1]
                                        if "802-11-wireless-security.key-mgmt" in args else "", "auto": False,
                                        "interface": args[args.index("ifname") + 1], "name": args[args.index("con-name") + 1]}
            output = "Connection '" + self.profiles[identifier]["name"] + "' (" + identifier + ") successfully added.\n"
        elif args[:2] == ["connection", "up"]:
            if "passwd-file" in args:
                path = args[args.index("passwd-file") + 1]
                self.credentials.append({"path": path, "content": Path(path).read_text(encoding="utf-8"),
                                         "mode": stat.S_IMODE(os.stat(path).st_mode)})
            self.active = args[args.index("uuid") + 1]
            output = "Connection successfully activated\n"
        elif args[:2] == ["connection", "modify"]:
            self.profiles[args[args.index("uuid") + 1]]["auto"] = True
            output = ""
        elif args[:2] == ["connection", "delete"]:
            self.profiles.pop(args[-1], None)
            output = ""
        elif args == ["radio", "wifi", "on"]:
            self.radio = "enabled"
            output = ""
        else:
            raise AssertionError("Unexpected fixture command: " + repr(args))
        return subprocess.CompletedProcess(argv, 0, output, "")


class WifiTests(unittest.TestCase):
    def manager(self, fixture=None, **kwargs):
        fixture = fixture or FakeNetworkManager()
        return WifiManager(runner=fixture, use_sudo=False, **kwargs), fixture

    def network(self, ssid="Home", security="WPA2", **kwargs):
        return {"ssid": ssid, "security": security, "interface": "wlan0", "bssid": "00:11:22:33:44:55", **kwargs}

    def test_nmcli_escaping_preserves_colons_backslashes_unicode_and_spaces(self):
        fields = ["*", "  墨:室\\AP  ", "00:11:22:33:44:55", "87", "WPA2"]
        self.assertEqual(split_terse(row(*fields)), fields)
        self.assertEqual(split_terse("x\\q:last\\"), ["x\\q", "last\\"])
        self.assertEqual(split_terse("a::"), ["a", "", ""])

    def test_inventory_only_selects_wireless_and_resolves_actual_ssid(self):
        manager, fixture = self.manager(FakeNetworkManager().saved("墨:室\\AP"))
        fixture.active = SAVED_UUID
        result = manager.inventory()
        self.assertTrue(result["ok"])
        self.assertEqual([item["name"] for item in result["interfaces"]], ["wlan0"])
        self.assertEqual(result["active"]["ssid"], "墨:室\\AP")
        self.assertEqual(result["active"]["address"], "192.168.7.24")
        self.assertEqual(result["active"]["uuid"], SAVED_UUID)
        self.assertEqual(len(result["saved"]), 1)
        self.assertNotIn(WIRED_UUID, json.dumps(result))

    def test_no_adapter_gives_actionable_result_and_does_not_scan(self):
        manager, fixture = self.manager()
        fixture.adapter = False
        result = manager.retry()
        self.assertFalse(result["available"])
        self.assertEqual(result["error_code"], "no-adapter")
        self.assertIn("USB Wi-Fi", result["error"])
        self.assertEqual(len(fixture.calls), 1)
        self.assertEqual(result["networks"], [])

    def test_scan_deduplicates_and_prefers_connected_access_point(self):
        manager, fixture = self.manager(FakeNetworkManager().saved("  墨:室\\AP  "))
        fixture.networks = [row("", "  墨:室\\AP  ", "00:11:22:33:44:55", 90, "WPA2"),
                            row("*", "  墨:室\\AP  ", "00:11:22:33:44:66", 40, "WPA2"),
                            row("", "  墨:室\\AP  ", "00:11:22:33:44:77", 95, "--"),
                            row("", "", "00:11:22:33:44:88", 80, "WPA2"),
                            row("", "Campus", "00:11:22:33:44:99", 70, "WPA2 802.1X"),
                            "malformed:row"]
        result = manager.scan()
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["networks"]), 3)
        self.assertEqual(result["networks"][0]["signal"], 40)
        self.assertTrue(result["networks"][0]["saved"])
        self.assertFalse(next(item for item in result["networks"] if item["ssid"] == "Campus")["connectable"])
        self.assertIn("--rescan", fixture.command(fixture.calls[-1][0]))

    def test_scan_refuses_ethernet_interface(self):
        manager, fixture = self.manager()
        result = manager.scan("eth0")
        self.assertFalse(result["ok"])
        self.assertFalse(any("list" in fixture.command(call[0]) for call in fixture.calls))

    def test_inventory_reads_disabled_radio_without_mutations(self):
        manager, fixture = self.manager()
        fixture.radio = "disabled"
        result = manager.inventory()
        self.assertIs(result["radio_enabled"], False)
        self.assertTrue(result["available"])
        self.assertFalse(any("on" in call[0] for call in fixture.calls))

    def test_explicit_scan_enables_only_wifi_radio_and_then_scans(self):
        manager, fixture = self.manager()
        fixture.radio = "disabled"
        result = manager.scan()
        self.assertTrue(result["ok"])
        self.assertIs(result["radio_enabled"], True)
        self.assertTrue(result["networks"])
        self.assertEqual(sum(fixture.command(call[0]) == ["radio", "wifi", "on"] for call in fixture.calls), 1)
        self.assertFalse(any("networking" in call[0] or "eth0" in call[0] for call in fixture.calls))

    def test_radio_enable_permission_failure_does_not_scan(self):
        manager, fixture = self.manager()
        fixture.radio = "disabled"
        def fail(args, options):
            if args == ["radio", "wifi", "on"]:
                return subprocess.CompletedProcess(args, 1, "", "Permission denied")
        fixture.fail = fail
        result = manager.retry()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "permission")
        self.assertFalse(any("list" in fixture.command(call[0]) for call in fixture.calls))

    def test_hardware_block_is_reported_after_radio_enable(self):
        manager, fixture = self.manager()
        fixture.radio = "disabled"
        def fail(args, options):
            if args == ["radio", "wifi", "on"]:
                return subprocess.CompletedProcess(args, 0, "", "")
        fixture.fail = fail
        result = manager.enable_radio()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "radio-blocked")

    def test_open_network_connects_and_saves_autoconnect(self):
        manager, fixture = self.manager()
        result = manager.connect(self.network("Cafe", "--"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["autoconnect"])
        self.assertEqual(result["active"]["ssid"], "Cafe")
        self.assertEqual(fixture.credentials, [])
        self.assertTrue(fixture.profiles[result["connection_uuid"]]["auto"])
        self.assertNotIn("802-11-wireless-security.key-mgmt", next(fixture.command(call[0]) for call in fixture.calls if "add" in call[0]))
        json.dumps(result)

    def test_psk_never_appears_in_arguments_and_credential_file_is_removed(self):
        manager, fixture = self.manager()
        password = "ab:$('x');\\!"
        result = manager.connect(self.network("A;$(whoami)\\:墨"), password)
        self.assertTrue(result["ok"])
        self.assertEqual(len(fixture.credentials), 1)
        credential = fixture.credentials[0]
        self.assertEqual(credential["content"], "802-11-wireless-security.psk:" + password + "\n")
        self.assertFalse(Path(credential["path"]).exists())
        if os.name == "posix":
            self.assertEqual(credential["mode"], 0o600)
        for argv, options in fixture.calls:
            self.assertNotIn(password, argv)
            self.assertIs(options["shell"], False)
            self.assertGreater(options["timeout"], 0)
            self.assertLessEqual(options["timeout"], 35)
            self.assertIs(options["stdin"], subprocess.DEVNULL)
        self.assertNotIn(password, json.dumps(result))

    def test_saved_connection_activates_verified_uuid_and_enables_autoconnect(self):
        manager, fixture = self.manager(FakeNetworkManager().saved())
        result = manager.connect(self.network(uuid=WIRED_UUID))
        self.assertTrue(result["ok"])
        self.assertEqual(result["connection_uuid"], SAVED_UUID)
        self.assertTrue(fixture.profiles[SAVED_UUID]["auto"])
        self.assertFalse(any("add" in call[0] for call in fixture.calls))
        self.assertFalse(any(WIRED_UUID in call[0] for call in fixture.calls))

    def test_new_password_preserves_existing_profile(self):
        manager, fixture = self.manager(FakeNetworkManager().saved(auto=True))
        result = manager.connect(self.network(), "new-password")
        self.assertTrue(result["ok"])
        self.assertNotEqual(result["connection_uuid"], SAVED_UUID)
        self.assertIn(SAVED_UUID, fixture.profiles)
        self.assertTrue(fixture.profiles[SAVED_UUID]["auto"])

    def test_wpa3_uses_sae_and_required_management_frame_protection(self):
        manager, fixture = self.manager()
        result = manager.connect(self.network("WPA3 AP", "WPA3"), "密码")
        self.assertTrue(result["ok"])
        added = next(fixture.command(call[0]) for call in fixture.calls if "add" in call[0])
        self.assertEqual(added[added.index("802-11-wireless-security.key-mgmt") + 1], "sae")
        self.assertEqual(added[added.index("802-11-wireless-security.pmf") + 1], "3")

    def test_transition_network_uses_wpa_psk(self):
        manager, fixture = self.manager()
        result = manager.connect(self.network(security="WPA2 WPA3"), "valid-passphrase")
        self.assertTrue(result["ok"])
        self.assertEqual(fixture.profiles[result["connection_uuid"]]["kind"], "wpa-psk")

    def test_hidden_network_explicitly_sets_hidden_profile_property(self):
        manager, fixture = self.manager()
        result = manager.connect(self.network(hidden=True, bssid=""), "valid-passphrase")
        self.assertTrue(result["ok"])
        added = next(fixture.command(call[0]) for call in fixture.calls if "add" in call[0])
        self.assertEqual(added[added.index("802-11-wireless.hidden") + 1], "yes")

    def test_invalid_inputs_are_rejected_before_any_command(self):
        cases = [(self.network("bad\nSSID"), "valid-passphrase"), (self.network("墨" * 11), "valid-passphrase"),
                 (self.network(interface="wlan0;rm -rf /"), "valid-passphrase"),
                 (self.network(bssid="00:11:22;rm -rf"), "valid-passphrase"),
                 (self.network(security="WPA2 802.1X"), "valid-passphrase"),
                 (self.network(security="WEP"), "valid-passphrase"),
                 (self.network(hidden="true"), "valid-passphrase"),
                 (self.network(), "short"), (self.network(), "abc\n12345"),
                 (self.network(security="--"), "unwanted-passphrase")]
        for network, password in cases:
            with self.subTest(network=network, password=password):
                manager, fixture = self.manager()
                result = manager.connect(network, password)
                self.assertFalse(result["ok"])
                self.assertEqual(fixture.calls, [])

    def test_unsaved_protected_network_needs_password(self):
        manager, fixture = self.manager()
        result = manager.connect(self.network())
        self.assertFalse(result["ok"])
        self.assertIn("password", result["error"])
        self.assertFalse(any("add" in call[0] for call in fixture.calls))

    def test_activation_timeout_cleans_only_new_wifi_profile_and_redacts_output(self):
        manager, fixture = self.manager(FakeNetworkManager().saved(auto=True))
        paths = []
        def fail(args, options):
            if args[:2] == ["connection", "up"]:
                paths.append(args[args.index("passwd-file") + 1])
                raise subprocess.TimeoutExpired(args, options["timeout"], output="secret-password")
        fixture.fail = fail
        result = manager.connect(self.network(), "secret-password")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "timeout")
        self.assertEqual(set(fixture.profiles), {SAVED_UUID})
        self.assertTrue(paths and all(not Path(path).exists() for path in paths))
        self.assertNotIn("secret-password", json.dumps(result))
        deleted = next(fixture.command(call[0]) for call in fixture.calls if "delete" in call[0])
        self.assertNotIn(SAVED_UUID, deleted)
        self.assertNotIn(WIRED_UUID, deleted)

    def test_failed_activation_error_does_not_return_password(self):
        manager, fixture = self.manager()
        def fail(args, options):
            if args[:2] == ["connection", "up"]:
                return subprocess.CompletedProcess(args, 4, "", "Rejected secret-password")
        fixture.fail = fail
        result = manager.connect(self.network(), "secret-password")
        self.assertFalse(result["ok"])
        self.assertIn("[redacted]", result["error"])
        self.assertNotIn("secret-password", json.dumps(result))

    def test_autoconnect_failure_reports_connected_without_claiming_saved(self):
        manager, fixture = self.manager()
        def fail(args, options):
            if args[:2] == ["connection", "modify"]:
                return subprocess.CompletedProcess(args, 1, "", "Permission denied")
        fixture.fail = fail
        result = manager.connect(self.network("Cafe", "--"))
        self.assertTrue(result["ok"])
        self.assertFalse(result["autoconnect"])
        self.assertTrue(result["warnings"])
        self.assertFalse(fixture.profiles[result["connection_uuid"]]["auto"])

    def test_inventory_deadline_bounds_multiple_commands(self):
        now = [0.0]
        manager, fixture = self.manager(clock=lambda: now[0], command_timeout=3, inventory_timeout=6)
        fixture.on_call = lambda: now.__setitem__(0, now[0] + 3)
        result = manager.inventory()
        self.assertTrue(result["available"])
        self.assertEqual(len(fixture.calls), 2)
        self.assertEqual(now[0], 6)
        self.assertTrue(any("timed out" in value for value in result["warnings"]))

    def test_missing_nmcli_and_status_timeout_are_serializable_failures(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired(["nmcli"], 3)):
            with self.subTest(error=type(error).__name__):
                manager = WifiManager(runner=mock.Mock(side_effect=error), use_sudo=False)
                result = manager.inventory()
                self.assertFalse(result["ok"])
                self.assertIn(result["error_code"], ("unavailable", "timeout"))
                json.dumps(result)

    def test_only_mutations_use_noninteractive_sudo(self):
        fixture = FakeNetworkManager()
        manager = WifiManager(runner=fixture)
        with mock.patch("connectivity.wifi.os.geteuid", return_value=1000, create=True):
            result = manager.connect(self.network("Cafe", "--"))
        self.assertTrue(result["ok"])
        for argv, options in fixture.calls:
            args = fixture.command(argv)
            mutation = args[:2] in (["connection", "add"], ["connection", "up"], ["connection", "modify"])
            self.assertEqual(argv[:3], ["sudo", "-n", "nmcli"] if mutation else ["nmcli", "--colors", "no"])

    def test_operations_never_disable_devices_or_restart_networkmanager(self):
        manager, fixture = self.manager()
        manager.retry()
        manager.connect(self.network("Cafe", "--"))
        for argv, options in fixture.calls:
            self.assertNotIn("systemctl", argv)
            self.assertNotIn("restart", argv)
            self.assertNotIn("disconnect", argv)
            self.assertNotIn("off", argv)
            self.assertNotIn("eth0", argv)


if __name__ == "__main__":
    unittest.main()
