"""Token-bounded KV260 target inspection and fused deploy/run/retrieve flow."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

from . import vision


class Kv260Error(ValueError):
    pass


_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}\Z")
_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,31}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_APP = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_ACTIONS = {"host_inspect", "inspect", "run", "dpu_inspect", "dpu_run"}
_BACKENDS = {"board", "loopback", "sw_emu", "hw_emu"}
_OUTPUT_LAYOUTS = {"axis_packed", "rgb24", "bgr24", "gray8", "image"}
_MAX_CAPTURE = 128 * 1024
_MAX_BOARD_OUTPUT_BYTES = 512 * 1024 * 1024
_MAX_DPU_THREADS = 16
_MAX_DPU_REPEAT = 10


def _validate_connection(host: str, user: str, port: int, ssh_key: str) -> dict:
    if not isinstance(host, str) or not _HOST.fullmatch(host.strip()):
        raise Kv260Error("host 必须是 IPv4、主机名或 mDNS 名称，不支持空格/命令字符")
    if not isinstance(user, str) or not _USER.fullmatch(user):
        raise Kv260Error("user 必须是安全的 Linux 用户名")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise Kv260Error("port 必须在 1..65535 之间")
    key = ""
    if ssh_key:
        candidate = Path(ssh_key).expanduser().resolve()
        if not candidate.is_file():
            raise Kv260Error(f"ssh_key 不存在: {ssh_key}")
        key = str(candidate)
    return {"host": host.strip(), "user": user, "port": port, "ssh_key": key}


def resource_key(host: str, port: int) -> str:
    """Return the canonical reservation key after validating the target."""
    connection = _validate_connection(host, "ubuntu", port, "")
    return f"lock:kv260:ssh:{connection['host'].casefold()}:{connection['port']}"


def offline_resource_key(session_dir: str) -> str:
    """Serialize generated offline artifacts for one vision session."""
    session = str(Path(session_dir).expanduser().resolve()).casefold()
    digest = hashlib.sha256(session.encode("utf-8")).hexdigest()[:20]
    return f"lock:kv260:offline:{digest}"


def _bounded(text: str, limit: int = _MAX_CAPTURE) -> str:
    value = text or ""
    return value if len(value) <= limit else value[-limit:]


def _tail(text: str, lines: int = 24) -> str:
    return "\n".join((text or "").splitlines()[-lines:])


def _run_process(args: list[str], timeout: int) -> dict:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, shell=False, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise Kv260Error(f"命令超时（{timeout}s）: {Path(args[0]).name}") from exc
    except OSError as exc:
        raise Kv260Error(f"无法启动 {Path(args[0]).name}: {exc}") from exc
    return {
        "returncode": result.returncode,
        "stdout": _bounded(result.stdout), "stderr": _bounded(result.stderr),
    }


_WINDOWS_USB_PROBE = r'''[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
$devices = @(Get-PnpDevice -PresentOnly | Where-Object {
  $_.InstanceId -match '^USB\\VID_0403&PID_6011' -or
  $_.InstanceId -match '^FTDIBUS\\VID_0403\+PID_6011'
} | ForEach-Object {
  [pscustomobject]@{
    status = $_.Status
    class = $_.Class
    name = $_.FriendlyName
    instance_id = $_.InstanceId
  }
})
ConvertTo-Json -Compress -InputObject @($devices)
'''


def _host_usb_snapshot(timeout: int = 15) -> dict:
    """Read only the host USB/UART inventory; never enumerate or mount storage."""
    if sys.platform.startswith("win"):
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not powershell:
            return {"transport": "windows_pnp", "devices": [], "error": "PowerShell unavailable"}
        probe = _run_process([
            powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
            "-Command", _WINDOWS_USB_PROBE,
        ], timeout)
        if probe["returncode"] != 0:
            return {
                "transport": "windows_pnp", "devices": [],
                "error": _tail(probe["stdout"] + probe["stderr"], 8),
            }
        try:
            devices = json.loads(probe["stdout"] or "[]")
        except json.JSONDecodeError:
            devices = []
        if isinstance(devices, dict):
            devices = [devices]
        return {
            "transport": "windows_pnp",
            "devices": [item for item in devices if isinstance(item, dict)][:16],
        }

    ports = sorted(
        str(path) for pattern in ("ttyUSB*", "ttyACM*", "tty.usbserial*")
        for path in Path("/dev").glob(pattern)
    )
    devices = []
    for vendor_file in Path("/sys/bus/usb/devices").glob("*/idVendor"):
        try:
            vendor = vendor_file.read_text(encoding="ascii").strip().casefold()
            product = (vendor_file.parent / "idProduct").read_text(encoding="ascii").strip().casefold()
        except OSError:
            continue
        if (vendor, product) != ("0403", "6011"):
            continue
        try:
            serial = (vendor_file.parent / "serial").read_text(encoding="utf-8").strip()
        except OSError:
            serial = ""
        devices.append({"status": "OK", "class": "USB", "name": "FTDI FT4232H",
                        "instance_id": serial})
    return {"transport": "posix_sysfs", "devices": devices, "ports": ports}


def inspect_host(timeout: int = 15, detail: str = "compact") -> dict:
    """Return a bounded pre-boot KV260 USB/UART contract without touching storage."""
    if detail not in {"compact", "standard"}:
        raise Kv260Error("detail 必须是 compact 或 standard")
    snapshot = _host_usb_snapshot(min(timeout, 30))
    devices = snapshot.get("devices", [])
    composite = next((
        item for item in devices
        if re.search(r"VID_0403&PID_6011\\([^\\]+)$", str(item.get("instance_id", "")), re.I)
    ), None)
    serial = ""
    if composite:
        match = re.search(
            r"VID_0403&PID_6011\\([^\\]+)$", str(composite.get("instance_id", "")), re.I
        )
        serial = match.group(1) if match else ""
    interfaces = sorted({
        int(match.group(1), 16)
        for item in devices
        if (match := re.search(r"&MI_([0-9A-F]{2})\\", str(item.get("instance_id", "")), re.I))
    })
    ports = []
    for item in devices:
        port_match = re.search(r"\((COM\d+)\)", str(item.get("name", "")), re.I)
        if not port_match:
            continue
        letter = ""
        if serial:
            interface_match = re.search(
                rf"PID_6011\+{re.escape(serial)}([A-D])\\",
                str(item.get("instance_id", "")), re.I,
            )
            letter = interface_match.group(1).upper() if interface_match else ""
        ports.append({"port": port_match.group(1).upper(), "interface": letter})
    if not ports:
        ports = [{"port": value, "interface": ""} for value in snapshot.get("ports", [])]
    ports.sort(key=lambda item: (item["interface"] or "Z", item["port"]))
    recommended = next((item["port"] for item in ports if item["interface"] == "B"), "")
    if not recommended and ports:
        recommended = ports[1 if len(ports) > 1 else 0]["port"]
    detected = bool(devices)
    failures = []
    if not detected:
        failures.append("kv260_ftdi_not_detected")
    if not recommended:
        failures.append("uart_not_enumerated")
    result = {
        "status": "ready" if not failures else "incomplete",
        "action": "host_inspect",
        "usb": {
            "detected": detected, "vid_pid": "0403:6011",
            "serial": serial, "interfaces": interfaces,
        },
        "uart": {
            "ports": ports, "recommended": recommended,
            "settings": {"baud": 115200, "data_bits": 8, "parity": "none",
                         "stop_bits": 1, "flow_control": "none"},
        },
        "failures": failures,
        "limitations": ["SD_CARD_NOT_ACCESSED", "LINUX_NOT_CHECKED", "XRT_NOT_CHECKED"],
        "next_action": "SD镜像完成并从卡启动后，再用 action=inspect 做一次SSH板端预检",
    }
    if snapshot.get("error"):
        result["diagnostic"] = str(snapshot["error"])[:500]
    if detail == "standard":
        result["host_probe"] = {
            "transport": snapshot.get("transport"), "devices": devices,
        }
    return result


class _SshTransport:
    def __init__(self, connection: dict, log_path: Path | None = None) -> None:
        self.connection = connection
        self.ssh_exe = shutil.which("ssh")
        self.scp_exe = shutil.which("scp")
        if not self.ssh_exe or not self.scp_exe:
            raise Kv260Error("主机缺少 OpenSSH ssh/scp；请先启用 Windows OpenSSH Client")
        self.log_path = log_path

    @property
    def target(self) -> str:
        return f"{self.connection['user']}@{self.connection['host']}"

    def _options(self, scp: bool = False) -> list[str]:
        port_flag = "-P" if scp else "-p"
        options = [
            port_flag, str(self.connection["port"]),
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "StrictHostKeyChecking=accept-new",
        ]
        if self.connection["ssh_key"]:
            options.extend(["-i", self.connection["ssh_key"]])
        return options

    def _record(self, label: str, result: dict) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            f"\n== {label} rc={result['returncode']} ==\n"
            f"{result.get('stdout', '')}{result.get('stderr', '')}"
        )
        with self.log_path.open("a", encoding="utf-8", errors="replace") as stream:
            stream.write(_bounded(body, 64 * 1024))

    def ssh(self, command: str, timeout: int, *, check: bool = True,
            label: str = "ssh") -> dict:
        result = _run_process(
            [self.ssh_exe, *self._options(), self.target, command], timeout
        )
        self._record(label, result)
        if check and result["returncode"] != 0:
            detail = _tail(result["stdout"] + result["stderr"])
            raise Kv260Error(f"KV260 {label} 失败（rc={result['returncode']}）: {detail}")
        return result

    def upload(self, source: Path, remote_path: str, timeout: int) -> None:
        result = _run_process(
            [self.scp_exe, *self._options(scp=True), str(source),
             f"{self.target}:{remote_path}"], timeout,
        )
        self._record(f"upload {source.name}", result)
        if result["returncode"] != 0:
            raise Kv260Error(f"上传 {source.name} 失败: {_tail(result['stdout'] + result['stderr'])}")

    def download(self, remote_path: str, destination: Path, timeout: int,
                 *, check: bool = True) -> bool:
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = _run_process(
            [self.scp_exe, *self._options(scp=True),
             f"{self.target}:{remote_path}", str(destination)], timeout,
        )
        self._record(f"download {destination.name}", result)
        if result["returncode"] == 0:
            return True
        if check:
            raise Kv260Error(
                f"回收 {destination.name} 失败: {_tail(result['stdout'] + result['stderr'])}"
            )
        return False


def _probe_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("XAB_KV260|"):
            continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            fields[parts[1]] = parts[2].strip()
    return fields


def _probe_script(detail: str = "compact") -> str:
    script = r'''probe_tool() {
  if command -v "$1" >/dev/null 2>&1; then
    printf 'XAB_KV260|tool_%s|yes\n' "$2"
  else
    printf 'XAB_KV260|tool_%s|no\n' "$2"
  fi
}
model="$(tr -d '\000' </proc/device-tree/model 2>/dev/null || true)"
os_name="$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-unknown}")"
printf 'XAB_KV260|model|%s\n' "$model"
printf 'XAB_KV260|arch|%s\n' "$(uname -m)"
printf 'XAB_KV260|kernel|%s\n' "$(uname -r)"
printf 'XAB_KV260|os|%s\n' "$os_name"
printf 'XAB_KV260|video_nodes|%s\n' "$(ls /dev/video* 2>/dev/null | tr '\n' ',' || true)"
printf 'XAB_KV260|media_nodes|%s\n' "$(ls /dev/media* 2>/dev/null | tr '\n' ',' || true)"
printf 'XAB_KV260|free_mb|%s\n' "$(df -Pm /tmp 2>/dev/null | awk 'NR==2 {print $4}')"
probe_tool xmutil xmutil
probe_tool xrt-smi xrt_smi
probe_tool xbutil xbutil
probe_tool xclbinutil xclbinutil
probe_tool v4l2-ctl v4l2_ctl
probe_tool media-ctl media_ctl
probe_tool gst-launch-1.0 gstreamer
'''
    if detail == "standard":
        script += r'''
cfg="/boot/config-$(uname -r)"
for symbol in VIDEO_XILINX SND_SOC_XILINX_PL_SND_CARD DRM_ZOCL; do
  mode=unknown
  if [ -r "$cfg" ]; then
    value=$(sed -n "s/^CONFIG_${symbol}=//p" "$cfg")
    case "$value" in y) mode=builtin;; m) mode=module;; esac
    if grep -q "^# CONFIG_${symbol} is not set$" "$cfg"; then mode=disabled; fi
  fi
  printf 'XAB_KV260|driver_%s|%s\n' "$symbol" "$mode"
done
'''
    return script


def _driver_config_summary(fields: dict) -> dict:
    modes = {}
    for name, symbol in (("video", "VIDEO_XILINX"),
                         ("audio_card", "SND_SOC_XILINX_PL_SND_CARD"),
                         ("zocl", "DRM_ZOCL")):
        value = fields.get("driver_" + symbol, "unknown")
        modes[name] = value if value in {"builtin", "module", "disabled"} else "unknown"
    return {"config_source": "/boot/config-<running-kernel>", "modes": modes,
            "builtin_requires_kernel_rebuild": any(v == "builtin" for v in modes.values()),
            "hot_unload_verified": False,
            "note": "Build mode only; module mode does not establish safe replacement or unload."}


def _xrt_device_count(output: str) -> int | None:
    """Return the enumerated XRT device count when the CLI output exposes it."""
    match = re.search(r"(?im)^\s*(\d+)\s+devices?\s+found\s*$", output)
    if match:
        return int(match.group(1))
    if re.search(r"(?im)device\s+ready\*?\s*:\s*yes\b", output):
        return 1
    bdf_rows = re.findall(r"(?im)^\s*\[[0-9a-f:.]+\]\s*:\s*", output)
    if bdf_rows:
        return len(bdf_rows)
    return None


def _command_succeeded(result: dict, *, reject: tuple[str, ...] = ()) -> bool:
    output = (result.get("stdout", "") + result.get("stderr", "")).casefold()
    return result.get("returncode") == 0 and not any(token in output for token in reject)


def _active_xmutil_app(output: str) -> str:
    """Only an unambiguous active table row may select a firmware directory."""
    names = {parts[0] for line in output.splitlines() if (parts := line.split())
             and len(parts) >= 2 and _APP.fullmatch(parts[0])
             and re.fullmatch(r"\d+,(?:\d+,)*", parts[-1])}
    return next(iter(names)) if len(names) == 1 else ""


def _xclbinutil_probe_command(app: str = "") -> str:
    # No firmware/app transition, temporary file, or output XCLBIN is created.
    directory = "/lib/firmware/xilinx/" + app if app and _APP.fullmatch(app) else ""
    script = r'''tool="$HOME/.local/share/xilinx-agent-bridge/xclbinutil-static-init/xclbinutil"
source=user_local
if ! test -x "$tool"; then tool=$(command -v xclbinutil || true); source=system; fi
if test -z "$tool"; then printf 'XAB_XCLBINUTIL|source|missing\n'; exit 127; fi
printf 'XAB_XCLBINUTIL|source|%s\nXAB_XCLBINUTIL|path|%s\n' "$source" "$tool"
if test "$source" = user_local; then printf 'XAB_XCLBINUTIL_WORKAROUND|1\n'; fi
hash=$(sha256sum "$tool" 2>/dev/null || true)
printf 'XAB_XCLBINUTIL|sha256|%s\n' "${hash%% *}"
version=$(timeout 10s "$tool" --version 2>&1); rc=$?
printf '%s\nXAB_XCLBINUTIL|version_rc|%s\n' "$version" "$rc"
test "$rc" -eq 0 || exit "$rc"
''' + "directory=" + shlex.quote(directory) + r'''
if test -z "$directory"; then printf 'XAB_XCLBINUTIL|active_check|no_unique_active_app\n'; exit 0; fi
set -- "$directory"/*.xclbin
if test "$#" -ne 1 || ! test -f "$1"; then
  printf 'XAB_XCLBINUTIL|active_check|no_unique_xclbin\n'; exit 0
fi
printf 'XAB_XCLBINUTIL|active_xclbin|%s\n' "$1"
info=$(timeout 10s "$tool" --input "$1" --info 2>&1); rc=$?
printf '%s\nXAB_XCLBINUTIL|info_rc|%s\nXAB_XCLBINUTIL|active_check|attempted\n' "$info" "$rc"
exit "$rc"
'''
    return "sh -lc " + shlex.quote(script)


def _xclbinutil_health(result: dict) -> dict:
    output = result.get("stdout", "") + result.get("stderr", "")
    fields = {key: value for key, value in re.findall(
        r"^XAB_XCLBINUTIL\|([a-z_0-9]+)\|([^\r\n]*)$", output, re.MULTILINE)}
    version_output = output.split("XAB_XCLBINUTIL|version_rc|", 1)[0]
    version = re.search(r"(?im)^\s*XRT Build Version:\s*(\d+\.\d+(?:\.\d+)?)\b", version_output)
    rejects = ("segmentation fault", "core dumped", "error:", "failed")
    clean_version = not any(token in version_output.casefold() for token in rejects)
    version_ready = bool(version and clean_version and fields.get("version_rc") == "0")
    uuid = re.search(r"(?im)^\s*UUID\s*\(xclbin\):\s*([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\b", output)
    attempted = fields.get("active_check") == "attempted"
    parsed = bool(attempted and version_ready and fields.get("info_rc") == "0"
                  and not any(token in output.casefold() for token in rejects)
                  and uuid and result.get("returncode") == 0)
    return {
        "source": fields.get("source", "unknown"), "path": fields.get("path", ""),
        "sha256": fields.get("sha256", "") if re.fullmatch(
            r"[0-9a-f]{64}", fields.get("sha256", "")) else "",
        "version": version.group(1) if version else "", "version_ready": version_ready,
        "active_check": fields.get("active_check", "not_checked"),
        "active_xclbin": fields.get("active_xclbin", ""),
        "active_parse_verified": parsed, "active_uuid": uuid.group(1) if parsed else "",
        "ready": version_ready and result.get("returncode") == 0 and (not attempted or parsed),
        "scope": "Selected executable only; does not establish system-tool repair or loaded-image identity.",
    }


def _check_unload_safety(transport, timeout: int) -> None:
    """Read-only guard for the physically observed Ubuntu VCU teardown Oops.

    Bound devices, not merely loaded modules, identify the hazardous configuration.
    This conservative gate is not a kernel fix or a guarantee for other kernels.
    """
    script = """set -eu
kernel=$(uname -r)
vcu=0
test -d /sys/bus/platform/devices
for device in /sys/bus/platform/devices/*; do
    test -L "$device/driver/module" || continue
    module=$(readlink "$device/driver/module")
    case "${module##*/}" in al5e|al5d|allegro|xlnx_vcu) vcu=1 ;; esac
done
version=$(cat /sys/module/xlnx_vcu/version 2>/dev/null || printf '-')
source=$(cat /sys/module/xlnx_vcu/srcversion 2>/dev/null || printf '-')
printf 'XAB_UNLOAD_CHECK|%s|%s|%s|%s\\n' "$kernel" "$vcu" "$version" "$source"
"""
    result = transport.ssh("sh -lc " + shlex.quote(script), timeout,
                           check=False, label="read-only unload safety")
    matches = re.findall(r"^XAB_UNLOAD_CHECK\|([^\s|]+)\|([01])\|([^\s|]+)\|([^\s|]+)$",
                         result.get("stdout", ""), re.MULTILINE)
    if result.get("returncode") != 0 or len(matches) != 1:
        raise Kv260Error("unload_safety_unknown: 未获得卸载前检查结果；未执行卸载")
    kernel, bound_vcu, version, source = matches[0]
    # The trial patch fixes one teardown order, but full-overlay refcount errors
    # remain. Even that exact module is NOT sufficient to waive the safety gate.
    # Custom builds can replace, not merely append to, the vendor suffix
    # (observed: 5.15.0-1027-bodyink1). A renamed 1027 kernel does not prove
    # that the driver/DT lifetime defects or their complete repair changed.
    affected_kernel = re.fullmatch(r"5\.15\.0-1027(?:[+.-].*)?", kernel)
    if affected_kernel and bound_vcu == "1":
        raise Kv260Error(
            "vcu_unload_known_failure: 当前内核有绑定的 VCU 驱动，热卸载存在已实测的 "
            "clk_core_disable Oops；未执行卸载。保留当前应用，先修复并验证驱动/DT 生命周期；"
            "不要自动重试、强制解绑或改用裸 JTAG")


def _xmutil_checked(transport, operation: str, timeout: int, app_name: str = "") -> None:
    """Old xmutil may return rc=0 after load Error:-1; require positive state evidence."""
    if operation not in {"loadapp", "unloadapp"}:
        raise Kv260Error("unsupported xmutil transition")
    if operation == "unloadapp":
        _check_unload_safety(transport, timeout)
    command = f"sudo -n xmutil {operation}"
    if operation == "loadapp":
        if not app_name:
            raise Kv260Error("xmutil load requires an application name")
        command += " " + shlex.quote(app_name)
    result = transport.ssh(command, timeout, check=False, label=f"xmutil {operation}")
    output = result.get("stdout", "") + result.get("stderr", "")
    success_pattern = (re.escape(app_name) + r":\s*loaded to slot\s+\d+\b"
                       if operation == "loadapp" else r"returns:\s*0\s*\(Ok\)")
    if (not _command_succeeded(result, reject=("error", "failed", "segmentation fault"))
            or not re.search(success_pattern, output, re.IGNORECASE)):
        raise Kv260Error(f"xmutil {operation} 未确认成功（不信任单独 rc=0）: {_tail(output)}")
    if operation == "loadapp":
        state = transport.ssh("sudo -n xmutil listapps", timeout, check=False,
                              label="verify loaded xmutil app")
        active = any(parts and parts[0] == app_name and
                     re.fullmatch(r"\d+,(?:\d+,)*", parts[-1])
                     for line in state.get("stdout", "").splitlines() if (parts := line.split()))
        if not _command_succeeded(state, reject=("error", "failed")) or not active:
            raise Kv260Error(f"xmutil active-slot 未确认目标应用: {app_name}")


def inspect_board(transport, timeout: int = 30, detail: str = "compact", *,
                  capture_camera: bool = False) -> dict:
    """Inspect metadata; frame acquisition requires explicit standard opt-in."""
    if detail not in {"compact", "standard"}:
        raise Kv260Error("detail 必须是 compact 或 standard")
    if not isinstance(capture_camera, bool):
        raise Kv260Error("capture_camera 必须是布尔值")
    if capture_camera and detail != "standard":
        raise Kv260Error("capture_camera=true 仅用于 detail=standard 的板端 inspect")
    script = "sh -lc " + shlex.quote(_probe_script(detail))
    probe = transport.ssh(script, timeout, label="inspect")
    fields = _probe_fields(probe["stdout"])
    tools = {
        name: fields.get(f"tool_{name}") == "yes"
        for name in (
            "xmutil", "xrt_smi", "xbutil", "xclbinutil",
            "v4l2_ctl", "media_ctl", "gstreamer",
        )
    }
    backend = "xrt-smi" if tools["xrt_smi"] else "xbutil" if tools["xbutil"] else ""
    xrt_result = {"returncode": -1, "stdout": "", "stderr": ""}
    if backend:
        examine = (
            f"{backend} examine >/tmp/xab-xrt.txt 2>&1; "
            "rc=$?; head -n 120 /tmp/xab-xrt.txt; "
            "rm -f /tmp/xab-xrt.txt; exit $rc"
        )
        remote = f"sh -lc {shlex.quote(examine)}"
        xrt_result = transport.ssh(remote, timeout, check=False, label="xrt examine")
    xmutil_result = {"returncode": -1, "stdout": "", "stderr": ""}
    xmutil_elevation = "unavailable"
    if tools["xmutil"]:
        xmutil_result = transport.ssh(
            "xmutil listapps", timeout,
            check=False, label="xmutil listapps",
        )
        xmutil_ok = _command_succeeded(
            xmutil_result,
            reject=("permission denied", "transport endpoint is not connected"),
        )
        if xmutil_ok:
            xmutil_elevation = "direct"
        else:
            sudo_result = transport.ssh(
                "sudo -n xmutil listapps", timeout,
                check=False, label="sudo xmutil listapps",
            )
            if _command_succeeded(
                sudo_result,
                reject=("password is required", "permission denied"),
            ):
                xmutil_result = sudo_result
                xmutil_ok = True
                xmutil_elevation = "sudo_n"
            else:
                xmutil_result = sudo_result
                xmutil_ok = False
                xmutil_elevation = "sudo_required"
    else:
        xmutil_ok = False

    model = fields.get("model", "")
    arch = fields.get("arch", "")
    video_nodes = [value for value in fields.get("video_nodes", "").split(",") if value]
    media_nodes = [value for value in fields.get("media_nodes", "").split(",") if value]
    v4l2_result = {"returncode": -1, "stdout": "", "stderr": ""}
    media_result = {"returncode": -1, "stdout": "", "stderr": ""}
    capture_result = {"returncode": -1, "stdout": "", "stderr": ""}
    capture_checked = capture_camera
    capture_attempted = False
    capture_bytes = 0
    capture_ready = False
    capture_device = ""
    capture_transport = ""
    capture_candidates: list[dict[str, str]] = []
    capture_attempts: list[dict[str, object]] = []
    xclbin_result = {"returncode": -1, "stdout": "", "stderr": ""}
    xclbin_ready: bool | None = None
    xclbin_workaround = False
    xclbin_health = None
    if detail == "standard":
        xclbin_result = transport.ssh(
            _xclbinutil_probe_command(_active_xmutil_app(xmutil_result.get("stdout", ""))
                                     if xmutil_ok else ""), timeout,
            check=False, label="xclbinutil version",
        )
        xclbin_workaround = "XAB_XCLBINUTIL_WORKAROUND|1" in xclbin_result.get("stdout", "")
        xclbin_health = _xclbinutil_health(xclbin_result)
        xclbin_ready = xclbin_health["ready"]
    if detail == "standard" and tools["v4l2_ctl"]:
        v4l2_result = transport.ssh(
            "v4l2-ctl --list-devices 2>&1 | head -n 80", timeout,
            check=False, label="v4l2 devices",
        )
    if detail == "standard" and tools["media_ctl"] and media_nodes:
        media_result = transport.ssh(
            f"media-ctl -d {shlex.quote(media_nodes[0])} -p 2>&1 | head -n 120",
            timeout, check=False, label="media graph",
        )
    if capture_camera and tools["v4l2_ctl"] and video_nodes:
        capture_checked = True
        # SmartCam can expose an idle MIPI node with no sensor attached, while
        # UVC cameras also expose metadata-only nodes. Query each bounded node's
        # capture format and try USB capture nodes before platform nodes.
        capture = r'''
count=0
probe_one() {
  dev="$1"
  transport="$2"
  if ! v4l2-ctl -d "$dev" --get-fmt-video >/dev/null 2>&1; then
    return 2
  fi
  printf 'XAB_CAMERA_CANDIDATE|device=%s|transport=%s\n' "$dev" "$transport"
  tmp=$(mktemp /tmp/xab-camera-probe.XXXXXX)
  log=$(timeout 12s v4l2-ctl -d "$dev" --stream-mmap=3 --stream-skip=2 \
    --stream-count=1 --stream-to="$tmp" 2>&1)
  rc=$?
  bytes=$(stat -c %s "$tmp" 2>/dev/null || printf 0)
  printf '%s\n' "$log" | tail -n 8
  printf 'XAB_CAMERA_ATTEMPT|device=%s|transport=%s|rc=%s|bytes=%s\n' \
    "$dev" "$transport" "$rc" "$bytes"
  rm -f "$tmp"
  [ "$rc" -eq 0 ] && [ "$bytes" -gt 0 ]
}
for pass in usb platform; do
  for dev in /dev/video*; do
    [ -e "$dev" ] || continue
    path=$(readlink -f "/sys/class/video4linux/${dev##*/}/device" 2>/dev/null || true)
    transport=platform
    case "$path" in *\/usb*) transport=usb ;; esac
    [ "$pass" = "$transport" ] || continue
    count=$((count + 1))
    [ "$count" -le 8 ] || exit 0
    if probe_one "$dev" "$transport"; then
      exit 0
    fi
  done
done
exit 0
'''
        capture_result = transport.ssh(
            "sh -lc " + shlex.quote(capture), timeout,
            check=False, label="camera capture probe",
        )
        capture_output = capture_result["stdout"] + capture_result["stderr"]
        for match in re.finditer(
            r"XAB_CAMERA_CANDIDATE\|device=([^|\r\n]+)\|transport=([^|\r\n]+)",
            capture_output,
        ):
            capture_candidates.append({
                "device": match.group(1), "transport": match.group(2),
            })
        for match in re.finditer(
            r"XAB_CAMERA_ATTEMPT\|device=([^|\r\n]+)\|transport=([^|\r\n]+)"
            r"\|rc=(\d+)\|bytes=(\d+)",
            capture_output,
        ):
            attempt = {
                "device": match.group(1), "transport": match.group(2),
                "returncode": int(match.group(3)), "bytes": int(match.group(4)),
            }
            capture_attempts.append(attempt)
        capture_attempted = bool(capture_attempts)
        winner = next((
            item for item in capture_attempts
            if item["returncode"] == 0 and item["bytes"] > 0
        ), capture_attempts[-1] if capture_attempts else None)
        if winner:
            capture_device = str(winner["device"])
            capture_transport = str(winner["transport"])
            capture_bytes = int(winner["bytes"])
            capture_ready = winner["returncode"] == 0 and capture_bytes > 0
    try:
        free_mb = int(fields.get("free_mb") or 0)
    except ValueError:
        free_mb = 0
    failures: list[str] = []
    if arch != "aarch64":
        failures.append(f"unexpected_arch={arch or 'unknown'}")
    identity = model.casefold()
    if not any(token in identity for token in ("kv260", "kria", "k26")):
        failures.append(f"unexpected_model={model or 'unknown'}")
    xrt_output = xrt_result["stdout"] + xrt_result["stderr"]
    xrt_count = _xrt_device_count(xrt_output)
    xrt_command_ok = xrt_result["returncode"] == 0
    xrt_ready = xrt_command_ok and xrt_count != 0
    if not backend:
        failures.append("xrt_cli_missing")
    elif not xrt_command_ok:
        failures.append("xrt_examine_failed")
    elif xrt_count == 0:
        failures.append("xrt_no_devices")
    if not xmutil_ok and not xrt_ready:
        failures.append(
            "xmutil_sudo_required" if tools["xmutil"] else "xmutil_missing"
        )
    issues: list[str] = []
    if xclbin_ready is False:
        issues.append("xclbinutil_failed")
    if capture_checked and not capture_ready:
        issues.append(
            "camera_capture_failed" if capture_attempted
            else "camera_capture_unavailable"
        )
    status = "ready" if not failures else "blocked"
    capture_probe = {
        "status": ("not_requested" if not capture_camera else "ready" if capture_ready
                   else "failed" if capture_attempted else "unavailable"),
        "checked": capture_checked,
        "attempted": capture_attempted,
        "ready": capture_ready,
        "bytes": capture_bytes,
    }
    if detail == "standard":
        capture_probe.update({
            "device": capture_device,
            "transport": capture_transport,
            "candidate_count": len(capture_candidates),
            "attempt_count": len(capture_attempts),
        })
    result = {
        "status": status,
        "target": transport.target,
        "model": model, "arch": arch, "os": fields.get("os", ""),
        "kernel": fields.get("kernel", ""),
        "runtime": {
            "xrt_backend": backend, "xrt_ready": xrt_ready,
            "xrt_device_count": xrt_count,
            "xmutil": xmutil_ok,
            "xmutil_installed": tools["xmutil"],
            "xmutil_elevation": xmutil_elevation,
            "xclbinutil_installed": tools["xclbinutil"] or bool(
                xclbin_health and xclbin_health["path"]),
            "xclbinutil_ready": xclbin_ready,
        },
        "video": {
            "video_nodes": video_nodes, "media_nodes": media_nodes,
            "gstreamer": tools["gstreamer"],
            "v4l2_ctl": tools["v4l2_ctl"], "media_ctl": tools["media_ctl"],
            "capture_probe": capture_probe,
        },
        "free_mb": free_mb,
        "failures": failures,
        "issues": issues,
        "next_action": (
            "加载一个已安装的 Kria app 后重新 inspect"
            if failures == ["xrt_no_devices"]
            else "为 bridge 用户配置 xmutil 的非交互 sudo 后重新 inspect"
            if "xmutil_sudo_required" in failures
            else "相机节点存在但单帧采集失败；检查传感器连接与 media format"
            if capture_checked and not capture_ready
            else "继续 board run"
            if not failures
            else "根据 failures 修复板端环境后重新 inspect"
        ),
    }
    if xclbin_workaround:
        result["runtime"]["xclbinutil_workaround"] = True
    if detail == "standard":
        from .display import PROBE, summarize_probe

        result["runtime"]["xclbinutil_health"] = xclbin_health
        result["runtime"]["driver_config"] = _driver_config_summary(fields)
        result["video"]["display"] = summarize_probe(transport.ssh(
            "python3 -c " + shlex.quote(PROBE), timeout,
            check=False, label="display probe",
        ))
        result["tools"] = tools
        result["diagnostics"] = {
            "xrt_examine": _tail(xrt_result["stdout"] + xrt_result["stderr"], 80),
            "xmutil_listapps": _tail(
                xmutil_result["stdout"] + xmutil_result["stderr"], 80
            ),
            "v4l2_devices": _tail(v4l2_result["stdout"] + v4l2_result["stderr"], 60),
            "media_graph": _tail(media_result["stdout"] + media_result["stderr"], 80),
            "camera_capture": _tail(
                capture_result["stdout"] + capture_result["stderr"], 40
            ),
            "xclbinutil_version": _tail(
                xclbin_result["stdout"] + xclbin_result["stderr"], 20
            ),
        }
    return result


def _local_artifact(path: str, label: str, required: bool = True) -> Path | None:
    if not path:
        if required:
            raise Kv260Error(f"{label} 不能为空")
        return None
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise Kv260Error(f"{label} 不存在: {path}")
    if not 1 <= candidate.stat().st_size <= 8 * 1024 * 1024 * 1024:
        raise Kv260Error(f"{label} 文件大小不合法: {candidate}")
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_zeros(size: int) -> str:
    digest = hashlib.sha256()
    block = bytes(1024 * 1024)
    remaining = size
    while remaining:
        count = min(remaining, len(block))
        digest.update(block[:count])
        remaining -= count
    return digest.hexdigest()


def _json_payload(text: str, label: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise Kv260Error(f"无法解析 {label} JSON")
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise Kv260Error(f"无法解析 {label} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise Kv260Error(f"{label} 未返回 JSON object")
    return value


def _remote_home(transport, timeout: int) -> str:
    probe = transport.ssh(
        "printf '%s\\n' \"$HOME\"", timeout, label="resolve board home"
    )
    home = probe["stdout"].strip().splitlines()[-1] if probe["stdout"].strip() else ""
    if (not home.startswith("/") or ".." in PurePosixPath(home).parts
            or not re.fullmatch(r"/[A-Za-z0-9_./-]{1,255}", home)):
        raise Kv260Error("KV260 返回了不安全的 HOME 路径")
    return home.rstrip("/")


def _dpu_environment(home: str) -> tuple[str, str]:
    runtime = f"{home}/.local/share/xilinx-agent-bridge/vart2-static-init"
    required = (
        f"{runtime}/libvart-xrt-device-handle.so.2",
        f"{runtime}/libvart-dpu-controller.so.2",
    )
    condition = " && ".join(f"test -f {shlex.quote(path)}" for path in required)
    prefix = (
        f"if {condition}; then export LD_LIBRARY_PATH={shlex.quote(runtime)}"
        "${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}; XAB_VART_FIX=1; "
        "else XAB_VART_FIX=0; fi; "
    )
    return prefix, runtime


def _dpu_command(transport, prefix: str, command: str, timeout: int,
                 label: str, *, check: bool = False) -> dict:
    return transport.ssh(
        "sh -lc " + shlex.quote(prefix + command), timeout,
        check=check, label=label,
    )


def _cached_upload(transport, source: Path, remote_dir: str, digest: str,
                   suffix: str, timeout: int) -> tuple[str, bool]:
    remote = f"{remote_dir}/{digest}{suffix}"
    verify = (
        f"test -f {shlex.quote(remote)} && "
        f"test \"$(sha256sum {shlex.quote(remote)} | cut -d' ' -f1)\" = {digest}"
    )
    present = transport.ssh(
        "sh -lc " + shlex.quote(verify), timeout,
        check=False, label=f"check cached {suffix.lstrip('.')}"
    )
    if present["returncode"] == 0:
        return remote, True
    temporary = f"{remote}.part-{os.getpid()}-{time.time_ns()}"
    transport.upload(source, temporary, timeout)
    finalize = (
        f"test \"$(sha256sum {shlex.quote(temporary)} | cut -d' ' -f1)\" = {digest} && "
        f"mv -f {shlex.quote(temporary)} {shlex.quote(remote)}"
    )
    transport.ssh(
        "sh -lc " + shlex.quote(finalize), timeout,
        label=f"verify uploaded {suffix.lstrip('.')}"
    )
    return remote, False


def _dpu_query(transport, prefix: str, timeout: int) -> tuple[dict | None, dict]:
    result = _dpu_command(
        transport, prefix,
        "printf 'XAB_VART_FIX=%s\\n' \"$XAB_VART_FIX\"; "
        f"timeout {min(timeout, 30)}s python3 -m xdputil query",
        timeout, "DPU query",
    )
    combined = result["stdout"] + result["stderr"]
    if result["returncode"] != 0:
        failure = (
            "vart_startup_crash" if re.search(r"segmentation fault|core dumped", combined, re.I)
            else "dpu_query_failed"
        )
        return None, {"failure": failure, "diagnostic": _tail(combined, 12)}
    try:
        payload = _json_payload(result["stdout"], "xdputil query")
    except Kv260Error as exc:
        return None, {"failure": "dpu_query_invalid", "diagnostic": str(exc)}
    kernels = payload.get("kernels") or []
    kernel = next((item for item in kernels if isinstance(item, dict)), None)
    if not kernel:
        return None, {"failure": "dpu_not_found", "diagnostic": "xdputil returned no kernel"}
    return {
        "cores": int((payload.get("DPU IP Spec") or {}).get("DPU Core Count", len(kernels))),
        "arch": str(kernel.get("DPU Arch", "")),
        "frequency_mhz": int(kernel.get("DPU Frequency (MHz)", 0)),
        "fingerprint": str(kernel.get("fingerprint", "")).casefold(),
        "cu_name": str(kernel.get("cu_name", "")),
        "runtime_workaround": "XAB_VART_FIX=1" in result["stdout"],
    }, {}


def _dpu_model(transport, prefix: str, remote_model: str,
               timeout: int) -> tuple[dict | None, dict]:
    result = _dpu_command(
        transport, prefix,
        f"timeout {min(timeout, 30)}s python3 -m xdputil xmodel "
        f"{shlex.quote(remote_model)} -l",
        timeout, "XMODEL inspect",
    )
    combined = result["stdout"] + result["stderr"]
    if result["returncode"] != 0:
        return None, {"failure": "xmodel_inspect_failed", "diagnostic": _tail(combined, 12)}
    try:
        payload = _json_payload(result["stdout"], "xdputil xmodel")
    except Kv260Error as exc:
        return None, {"failure": "xmodel_inspect_invalid", "diagnostic": str(exc)}
    subgraphs = payload.get("subgraphs") or []
    indexed = [
        (index, item) for index, item in enumerate(subgraphs)
        if isinstance(item, dict) and str(item.get("device", "")).upper() == "DPU"
    ]
    if not indexed:
        return None, {"failure": "xmodel_has_no_dpu_subgraph", "diagnostic": ""}
    index, graph = indexed[0]
    inputs = graph.get("input_tensor") or []
    shape_text = str(inputs[0].get("shape", "")) if inputs and isinstance(inputs[0], dict) else ""
    shape = [int(value) for value in re.findall(r"\d+", shape_text)]
    if len(shape) < 2 or any(value <= 0 for value in shape):
        return None, {"failure": "xmodel_input_shape_invalid", "diagnostic": shape_text}
    sample_elements = 1
    for value in shape[1:]:
        sample_elements *= value
    return {
        "name": str(graph.get("name", ""))[-160:],
        "subgraph_index": index,
        "arch": str(graph.get("DPU Arch", "")),
        "fingerprint": str(graph.get("fingerprint", "")).casefold(),
        "input_shape": shape,
        "input_bytes": sample_elements,
        "outputs": [
            {"name": str(item.get("name", ""))[-160:],
             "shape": [int(value) for value in re.findall(r"\d+", str(item.get("shape", "")))]}
            for item in (graph.get("output_tensor") or [])[:8] if isinstance(item, dict)
        ],
    }, {}


def dpu_flow(transport, model: str = "", dpu_input: str = "",
             dpu_threads: int = 1, benchmark_seconds: int = 0,
             repeat: int = 1, timeout: int = 120,
             detail: str = "compact", *, run: bool = False) -> dict:
    if (isinstance(dpu_threads, bool) or not isinstance(dpu_threads, int)
            or not 1 <= dpu_threads <= _MAX_DPU_THREADS):
        raise Kv260Error(f"dpu_threads 必须是 1..{_MAX_DPU_THREADS} 的整数")
    if (isinstance(benchmark_seconds, bool) or not isinstance(benchmark_seconds, int)
            or not 0 <= benchmark_seconds <= 60):
        raise Kv260Error("benchmark_seconds 必须是 0..60 的整数")
    if (isinstance(repeat, bool) or not isinstance(repeat, int)
            or not 1 <= repeat <= _MAX_DPU_REPEAT):
        raise Kv260Error(f"repeat 必须是 1..{_MAX_DPU_REPEAT} 的整数")
    local_model = _local_artifact(model, "model", required=run or bool(model))
    local_input = _local_artifact(dpu_input, "dpu_input", required=False)
    if local_input is not None and local_model is None:
        raise Kv260Error("dpu_input 只能与 model 一起使用")
    home = _remote_home(transport, timeout)
    prefix, runtime_dir = _dpu_environment(home)
    board, problem = _dpu_query(transport, prefix, timeout)
    if board is None:
        result = {
            "status": "blocked", "action": "dpu_run" if run else "dpu_inspect",
            "target": transport.target, "failures": [problem["failure"]],
            "next_action": "修复 VART/xdputil 启动或加载可用 DPU overlay 后重试",
        }
        if detail == "standard":
            result["diagnostic"] = problem.get("diagnostic", "")
        return result
    result = {
        "status": "ready", "action": "dpu_run" if run else "dpu_inspect",
        "target": transport.target, "dpu": board, "failures": [],
        "next_action": "提供匹配指纹的 XMODEL 后执行 dpu_run" if not run else "DPU 推理完成",
    }
    if local_model is None:
        if detail == "standard" and board["runtime_workaround"]:
            result["runtime_override"] = runtime_dir
        return result

    cache_dir = f"{home}/.cache/xilinx-agent-bridge/dpu"
    transport.ssh(
        f"mkdir -p {shlex.quote(cache_dir)}", timeout, label="create DPU cache"
    )
    model_digest = _sha256_file(local_model)
    remote_model, model_hit = _cached_upload(
        transport, local_model, cache_dir, model_digest, ".xmodel", timeout
    )
    model_info, problem = _dpu_model(transport, prefix, remote_model, timeout)
    if model_info is None:
        result.update({
            "status": "blocked", "model": {"sha256": model_digest},
            "failures": [problem["failure"]],
            "next_action": "检查 XMODEL 完整性与板端 Vitis AI runtime 兼容性",
        })
        if detail == "standard":
            result["diagnostic"] = problem.get("diagnostic", "")
        return result
    model_info["sha256"] = model_digest
    result["model"] = model_info
    result["cache"] = {"model_hit": model_hit}
    compatible = bool(
        board["fingerprint"] and model_info["fingerprint"]
        and board["fingerprint"] == model_info["fingerprint"]
    )
    result["compatible"] = compatible
    if not compatible:
        result.update({
            "status": "incompatible", "failures": ["dpu_fingerprint_mismatch"],
            "next_action": "用与 board fingerprint 精确匹配的 arch/compiler 重新编译 XMODEL",
        })
        return result
    if not run:
        result["next_action"] = "指纹门禁通过；可直接执行 dpu_run"
        if detail == "standard" and board["runtime_workaround"]:
            result["runtime_override"] = runtime_dir
        return result

    input_hit = False
    if local_input is not None:
        actual = local_input.stat().st_size
        if actual != model_info["input_bytes"]:
            raise Kv260Error(
                f"dpu_input 大小与单样本张量不符: actual={actual}, "
                f"expected={model_info['input_bytes']}"
            )
        input_digest = _sha256_file(local_input)
        remote_input, input_hit = _cached_upload(
            transport, local_input, cache_dir, input_digest, ".input.bin", timeout
        )
        input_source = "file"
    else:
        input_digest = _sha256_zeros(model_info["input_bytes"])
        remote_input = f"{cache_dir}/{input_digest}.input.bin"
        verify_zero = (
            f"test -f {shlex.quote(remote_input)} && "
            f"test \"$(sha256sum {shlex.quote(remote_input)} | cut -d' ' -f1)\" "
            f"= {input_digest}"
        )
        cached_zero = transport.ssh(
            "sh -lc " + shlex.quote(verify_zero), timeout,
            check=False, label="check cached DPU input",
        )
        input_hit = cached_zero["returncode"] == 0
        if not input_hit:
            generate = (
                f"head -c {model_info['input_bytes']} /dev/zero > "
                f"{shlex.quote(remote_input)} && {verify_zero}"
            )
            transport.ssh(
                "sh -lc " + shlex.quote(generate), timeout,
                label="prepare deterministic DPU input",
            )
        input_source = "zero"
    result["cache"]["input_hit"] = input_hit
    result["input"] = {
        "source": input_source, "bytes": model_info["input_bytes"],
        "sha256": input_digest,
    }

    run_hashes: list[list[str]] = []
    first_outputs: list[dict] = []
    elapsed_total = 0.0
    for index in range(repeat):
        run_dir = f"{cache_dir}/run-{model_digest[:12]}-{time.time_ns()}-{index}"
        command = (
            f"mkdir -p {shlex.quote(run_dir)} && cd {shlex.quote(run_dir)} && "
            f"timeout {max(5, timeout - 10)}s python3 -m xdputil run "
            f"{shlex.quote(remote_model)} -i {model_info['subgraph_index']} "
            f"{shlex.quote(remote_input)} && "
            "for f in ./*.bin; do test -f \"$f\" || continue; "
            "n=${f#./}; s=$(stat -c %s \"$f\"); h=$(sha256sum \"$f\" | cut -d' ' -f1); "
            "printf 'XAB_DPU_OUTPUT|%s|%s|%s\\n' \"$n\" \"$s\" \"$h\"; done"
        )
        started = time.monotonic()
        executed = _dpu_command(
            transport, prefix, command, timeout, f"DPU inference {index + 1}"
        )
        elapsed_total += time.monotonic() - started
        if executed["returncode"] != 0:
            result.update({
                "status": "failed", "failures": ["dpu_inference_failed"],
                "next_action": "查看 compact failure；仅排障时用 detail=standard 获取日志尾部",
            })
            if detail == "standard":
                result["diagnostic"] = _tail(executed["stdout"] + executed["stderr"], 20)
            return result
        outputs = []
        for line in executed["stdout"].splitlines():
            if not line.startswith("XAB_DPU_OUTPUT|"):
                continue
            _, name, size, digest = line.split("|", 3)
            if not _NAME.fullmatch(name) or not size.isdigit() or not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
            outputs.append({
                "name": name, "bytes": int(size), "sha256": digest,
                "remote": f"{run_dir}/{name}",
            })
        if not outputs:
            result.update({
                "status": "failed", "failures": ["dpu_output_missing"],
                "next_action": "检查模型输出张量与 xdputil runtime",
            })
            return result
        hashes = [item["sha256"] for item in outputs]
        run_hashes.append(hashes)
        if index == 0:
            first_outputs = outputs

    deterministic = all(value == run_hashes[0] for value in run_hashes[1:])
    output_dir = local_model.parent / ".xab-kv260" / model_digest[:16]
    artifacts = []
    for item in first_outputs:
        destination = output_dir / item["name"]
        transport.download(item["remote"], destination, timeout)
        local_digest = _sha256_file(destination)
        if destination.stat().st_size != item["bytes"] or local_digest != item["sha256"]:
            result.update({
                "status": "failed", "failures": ["dpu_output_transfer_corrupt"],
                "next_action": "检查 SSH/SCP 链路后重试；损坏输出未被接受",
            })
            return result
        artifacts.append({
            "path": str(destination), "bytes": item["bytes"],
            "sha256": item["sha256"],
        })
    result["run"] = {
        "repeat": repeat, "deterministic": deterministic,
        "elapsed_seconds": round(elapsed_total, 6),
        "outputs": artifacts,
    }
    if repeat > 1 and not deterministic:
        result.update({
            "status": "failed", "failures": ["dpu_output_not_deterministic"],
            "next_action": "检查输入初始化、模型状态和 runtime 同步",
        })
        return result

    if benchmark_seconds:
        benchmark = _dpu_command(
            transport, prefix,
            f"SLEEP_MS={benchmark_seconds * 1000} timeout {benchmark_seconds + 10}s "
            f"python3 -m xdputil benchmark {shlex.quote(remote_model)} {dpu_threads}",
            min(timeout, benchmark_seconds + 20), "DPU benchmark",
        )
        output = benchmark["stdout"] + benchmark["stderr"]
        match = re.search(r"FPS=\s*([0-9.]+)", output)
        passed = benchmark["returncode"] == 0 and "Test PASS." in output and bool(match)
        result["benchmark"] = {
            "status": "pass" if passed else "failed",
            "seconds": benchmark_seconds, "threads": dpu_threads,
            "fps": float(match.group(1)) if match else None,
        }
        if not passed:
            result.update({
                "status": "failed", "failures": ["dpu_benchmark_failed"],
                "next_action": "确认 DPU overlay、温度和 VART runtime 后重试",
            })
            if detail == "standard":
                result["benchmark"]["diagnostic"] = _tail(output, 20)
            return result
    if detail == "standard" and board["runtime_workaround"]:
        result["runtime_override"] = runtime_dir
    return result


def _host_artifact(path: str) -> tuple[Path, dict]:
    candidate = _local_artifact(path, "host_executable")
    assert candidate is not None
    with candidate.open("rb") as stream:
        header = stream.read(64)
    if header.startswith(b"#!"):
        if b"\r" in header.split(b"\n", 1)[0]:
            raise Kv260Error("host_executable 脚本 shebang 使用 CRLF；请转换为 Linux LF")
        return candidate, {"kind": "script", "architecture": "target_interpreter"}
    if len(header) < 20 or header[:4] != b"\x7fELF":
        raise Kv260Error("host_executable 必须是 AArch64 ELF 或带 shebang 的 Linux 脚本")
    if header[4] != 2 or header[5] != 1:
        raise Kv260Error("host_executable 必须是 64-bit little-endian ELF")
    machine = int.from_bytes(header[18:20], "little")
    if machine != 183:  # ELF EM_AARCH64
        raise Kv260Error(f"host_executable ELF 架构不是 AArch64（e_machine={machine}）")
    return candidate, {"kind": "elf", "architecture": "aarch64"}


def _run_tokens(run_args: list[str] | None, frame: dict,
                has_xclbin: bool, output_name: str) -> list[str]:
    mapping = {
        "input": "input.bin", "output": output_name,
        "xclbin": "kernel.xclbin", "width": str(frame["width"]),
        "height": str(frame["height"]), "pixel_format": frame["pixel_format"],
        "pixels_per_clock": str(frame["pixels_per_clock"]),
    }
    if run_args is None:
        tokens = [
            "--input", "{input}", "--output", "{output}",
            "--width", "{width}", "--height", "{height}",
            "--format", "{pixel_format}", "--ppc", "{pixels_per_clock}",
        ]
        if has_xclbin:
            tokens[0:0] = ["--xclbin", "{xclbin}"]
    else:
        if not isinstance(run_args, list) or not 1 <= len(run_args) <= 64:
            raise Kv260Error("run_args 必须是 1..64 个参数的数组")
        tokens = run_args
    expanded: list[str] = []
    for token in tokens:
        if not isinstance(token, str) or len(token) > 1024 or any(ord(ch) < 32 for ch in token):
            raise Kv260Error("run_args 每项必须是不含控制字符、最长 1024 字符的文本")
        try:
            value = token.format_map(mapping)
        except (KeyError, ValueError) as exc:
            raise Kv260Error(
                "run_args 占位符仅支持 input/output/xclbin/width/height/"
                "pixel_format/pixels_per_clock"
            ) from exc
        expanded.append(value)
    if sum(len(value) for value in expanded) > 8192:
        raise Kv260Error("run_args 展开后总长度不能超过 8192")
    return expanded


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _offline_inspection(backend: str, detail: str) -> dict:
    limitations = ["NOT_TESTED_ON_HARDWARE"]
    if backend in {"sw_emu", "hw_emu"}:
        limitations.append("REAL_VITIS_XRT_EMULATION_NOT_TESTED")
    result = {
        "status": "ready", "target": f"local:{backend}",
        "model": "KV260 offline contract", "arch": "local",
        "os": sys.platform, "kernel": "not_applicable",
        "runtime": {
            "backend": backend,
            "process_required": backend in {"sw_emu", "hw_emu"},
            "hardware_verified": False,
        },
        "video": {
            "video_nodes": [], "media_nodes": [], "gstreamer": False,
            "v4l2_ctl": False, "media_ctl": False,
            "capture_probe": {"attempted": False, "ready": False, "bytes": 0},
        },
        "free_mb": 0, "failures": [],
        "limitations": limitations,
    }
    if detail == "standard":
        result["video"]["capture_probe"].update({
            "checked": False, "device": "", "transport": "",
            "candidate_count": 0, "attempt_count": 0,
        })
        result["diagnostics"] = {
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "purpose": (
                "验证 session、host 参数、buffer layout、输出归一化与视觉门禁；"
                "不替代真实 KV260/XRT/摄像头验证"
            ),
        }
    return result


def _native_host_artifact(path: str) -> tuple[Path, dict, list[str]]:
    candidate = _local_artifact(path, "host_executable")
    assert candidate is not None
    suffix = candidate.suffix.casefold()
    if suffix == ".py":
        return candidate, {
            "kind": "python_script", "architecture": "local_interpreter",
        }, [sys.executable, "host_app.py"]
    if os.name == "nt" and suffix not in {".exe", ".com"}:
        raise Kv260Error(
            "sw_emu/hw_emu 在 Windows 上的 host_executable 必须是 .exe/.com 或 .py"
        )
    if os.name != "nt" and not os.access(candidate, os.X_OK):
        raise Kv260Error("sw_emu/hw_emu 的 host_executable 没有本机执行权限")
    local_name = "host_app" + suffix
    return candidate, {
        "kind": "native_executable", "architecture": "local",
    }, [local_name]


def _offline_flow(session: Path, session_state: dict, backend: str,
                  host_executable: str, xclbin: str,
                  run_args: list[str] | None, output_name: str,
                  output_layout: str, stride_bytes: int,
                  timeout: int, detail: str) -> dict:
    input_bin = _local_artifact(session_state["paths"]["input_bin"], "session input.bin")
    assert input_bin is not None
    frame = session_state["frame"]
    executable: Path | None = None
    executable_profile: dict = {"kind": "none", "architecture": "none"}
    command_prefix: list[str] = []
    kernel: Path | None = None
    if backend == "loopback":
        if host_executable or xclbin or run_args:
            raise Kv260Error(
                "loopback 不执行 host/xclbin/run_args；需要运行程序请使用 sw_emu/hw_emu"
            )
        if output_layout != "axis_packed" or stride_bytes:
            raise Kv260Error("loopback 只支持 session 原生 axis_packed 输出")
        tokens: list[str] = []
    else:
        executable, executable_profile, command_prefix = _native_host_artifact(host_executable)
        kernel = _local_artifact(xclbin, "xclbin")
        assert kernel is not None
        tokens = _run_tokens(run_args, frame, True, output_name)

    fingerprint_payload = {
        "schema_version": 1, "backend": backend,
        "input_sha256": _file_sha256(input_bin),
        "host_sha256": _file_sha256(executable) if executable else "",
        "xclbin_sha256": _file_sha256(kernel) if kernel else "",
        "run_args": tokens, "output_name": output_name,
        "output_layout": output_layout, "stride_bytes": stride_bytes,
        "frame": frame,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    offline_dir = session / "offline" / backend
    offline_dir.mkdir(parents=True, exist_ok=True)
    output_path = offline_dir / output_name
    run_log = offline_dir / "run.log"
    evidence_path = offline_dir / "run.json"

    cached = False
    prior: dict = {}
    if evidence_path.is_file() and output_path.is_file() and run_log.is_file():
        try:
            prior = json.loads(evidence_path.read_text(encoding="utf-8"))
            cached = (
                prior.get("fingerprint") == fingerprint
                and prior.get("output_sha256") == _file_sha256(output_path)
            )
        except (OSError, ValueError, TypeError):
            cached = False

    started = time.monotonic()
    if not cached:
        if output_path.exists():
            output_path.unlink()
        if backend == "loopback":
            shutil.copyfile(input_bin, output_path)
            run_log.write_text(
                "XAB_KV260_OFFLINE|backend=loopback|result=pass\n",
                encoding="utf-8",
            )
        else:
            assert executable is not None and kernel is not None
            local_host_name = "host_app.py" if executable.suffix.casefold() == ".py" else command_prefix[0]
            local_host = (offline_dir / local_host_name).resolve()
            local_input = (offline_dir / "input.bin").resolve()
            local_kernel = (offline_dir / "kernel.xclbin").resolve()
            for source, destination in (
                (executable, local_host), (input_bin, local_input), (kernel, local_kernel)
            ):
                if source.resolve() != destination:
                    shutil.copy2(source, destination)
            environment = os.environ.copy()
            environment["XCL_EMULATION_MODE"] = backend
            command = (
                [sys.executable, str(local_host), *tokens]
                if executable.suffix.casefold() == ".py"
                else [str(local_host), *tokens]
            )
            try:
                process = subprocess.run(
                    command, cwd=offline_dir, env=environment, shell=False,
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise Kv260Error(
                    f"offline_timeout: {backend} host application 超过 {timeout}s"
                ) from exc
            except OSError as exc:
                raise Kv260Error(f"offline_launch_failed: {exc}") from exc
            run_log.write_text(
                _bounded(process.stdout + process.stderr), encoding="utf-8"
            )
            if process.returncode != 0:
                raise Kv260Error(
                    f"offline_host_failed: {backend} host application rc={process.returncode}: "
                    f"{_tail(process.stdout + process.stderr)}；日志: {run_log}"
                )
            if not output_path.is_file():
                raise Kv260Error(
                    f"offline_output_missing: host 未生成约定输出 {output_path}"
                )

    if not output_path.is_file():
        raise Kv260Error(f"offline_output_missing: {output_path}")
    output_size = output_path.stat().st_size
    if not 1 <= output_size <= _MAX_BOARD_OUTPUT_BYTES:
        raise Kv260Error(
            f"offline_output_size: 输出大小必须在 1..{_MAX_BOARD_OUTPUT_BYTES} 字节，实际 {output_size}"
        )
    try:
        normalized = (
            str(output_path) if output_layout == "image"
            else vision.raw_frame_to_png(
                str(session), str(output_path), output_layout, stride_bytes
            )
        )
        evaluation = vision.evaluate_session(str(session), normalized)
    except vision.VisionError as exc:
        raise Kv260Error(f"offline_output_contract: {exc}") from exc
    elapsed = round(time.monotonic() - started, 6)
    evidence = {
        "schema_version": 1, "completed": time.time(),
        "backend": backend, "hardware_verified": False,
        "fingerprint": fingerprint,
        "fingerprint_inputs": fingerprint_payload,
        "output_sha256": _file_sha256(output_path),
        "host_executable": str(executable or ""),
        "host_profile": executable_profile, "xclbin": str(kernel or ""),
        "run_args": tokens, "output_layout": output_layout,
        "stride_bytes": stride_bytes, "elapsed_seconds": elapsed,
        "raw_output": str(output_path), "normalized_output": normalized,
        "run_log": str(run_log), "evaluation": evaluation,
    }
    temp = evidence_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(evidence_path)
    result = {
        "status": evaluation["status"], "action": "run", "backend": backend,
        "target": f"local:{backend}", "hardware_verified": False,
        "limitations": _offline_inspection(backend, "compact")["limitations"],
        "cache": {
            "hit": cached,
            "process_launches": 0 if cached or backend == "loopback" else 1,
            "process_launches_saved": 1 if cached and backend != "loopback" else 0,
        },
        "run": {"returncode": 0, "elapsed_seconds": elapsed},
        "evaluation": vision.project_evaluation(evaluation, detail),
        "artifacts": {
            "output": normalized, "run_log": str(run_log),
            "evidence": str(evidence_path),
        },
        "next_action": (
            "离线输出已通过黄金门禁；仍需真实 KV260 验证"
            if evaluation["status"] == "pass"
            else "打开现有 review UI 人工确认；仍需真实 KV260 验证"
            if evaluation["status"] == "review_required"
            else "查看差分图与离线日志，修复后重跑同一 session"
        ),
    }
    if detail == "standard":
        result["inspection"] = _offline_inspection(backend, detail)
        result["fingerprint"] = fingerprint
        result["deployed"] = {
            "input_bin": str(input_bin), "host_executable": str(executable or ""),
            "host_profile": executable_profile, "xclbin": str(kernel or ""),
            "raw_output": str(output_path),
        }
    return result


def board_flow(host: str = "", session_dir: str = "", action: str = "inspect",
               user: str = "ubuntu", port: int = 22, ssh_key: str = "",
               app_name: str = "", load_app: bool = False,
               unload_active: bool = False, host_executable: str = "",
               xclbin: str = "", run_args: list[str] | None = None,
               output_name: str = "output.bin",
               output_layout: str = "axis_packed", stride_bytes: int = 0,
               timeout: int = 120, detail: str = "compact", *,
               backend: str = "board", model: str = "", dpu_input: str = "",
               dpu_threads: int = 1, benchmark_seconds: int = 0,
               repeat: int = 1, capture_camera: bool = False, _transport=None) -> dict:
    if action not in _ACTIONS:
        raise Kv260Error(
            "action 必须是 host_inspect、inspect、run、dpu_inspect 或 dpu_run"
        )
    if backend not in _BACKENDS:
        raise Kv260Error("backend 必须是 board/loopback/sw_emu/hw_emu")
    if detail not in {"compact", "standard"}:
        raise Kv260Error("detail 必须是 compact 或 standard")
    if not isinstance(capture_camera, bool):
        raise Kv260Error("capture_camera 必须是布尔值")
    if capture_camera and not (action == "inspect" and backend == "board" and detail == "standard"):
        raise Kv260Error("capture_camera=true 仅用于 board inspect 且 detail=standard")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 5 <= timeout <= 3600:
        raise Kv260Error("timeout 必须在 5..3600 秒之间")
    if not isinstance(load_app, bool) or not isinstance(unload_active, bool):
        raise Kv260Error("load_app/unload_active 必须是布尔值")
    if action == "host_inspect":
        if (backend != "board" or host or session_dir or ssh_key or app_name
                or load_app or unload_active or host_executable or xclbin or run_args
                or user != "ubuntu" or port != 22):
            raise Kv260Error("host_inspect 只接受 timeout/detail，不接受SD、SSH、session或运行参数")
        return inspect_host(timeout, detail)
    if action == "inspect":
        if backend != "board":
            if host or ssh_key or app_name or load_app or unload_active or user != "ubuntu" or port != 22:
                raise Kv260Error(
                    "离线 backend 不接受 host/SSH/xmutil 参数；只传 backend/detail"
                )
            return _offline_inspection(backend, detail)
        connection = _validate_connection(host, user, port, ssh_key)
        transport = _transport or _SshTransport(connection)
        return inspect_board(transport, min(timeout, 120), detail, capture_camera=capture_camera)

    if action in {"dpu_inspect", "dpu_run"}:
        if backend != "board":
            raise Kv260Error("dpu_inspect/dpu_run 只支持物理 board backend")
        if (session_dir or app_name or load_app or unload_active or host_executable
                or xclbin or run_args or output_name != "output.bin"
                or output_layout != "axis_packed" or stride_bytes):
            raise Kv260Error(
                "DPU 动作不接受视觉 session、host/xclbin、xmutil 或通用输出参数"
            )
        connection = _validate_connection(host, user, port, ssh_key)
        transport = _transport or _SshTransport(connection)
        return dpu_flow(
            transport, model, dpu_input, dpu_threads, benchmark_seconds,
            repeat, timeout, detail, run=action == "dpu_run",
        )

    if output_layout not in _OUTPUT_LAYOUTS:
        raise Kv260Error("output_layout 必须是 axis_packed/rgb24/bgr24/gray8/image")
    if isinstance(stride_bytes, bool) or not isinstance(stride_bytes, int) or not 0 <= stride_bytes <= 1_048_576:
        raise Kv260Error("stride_bytes 必须是 0..1048576 的整数")
    if output_layout in {"axis_packed", "image"} and stride_bytes:
        raise Kv260Error(f"{output_layout} 不接受 stride_bytes")
    if (not _NAME.fullmatch(output_name) or output_name in {".", ".."}
            or output_name.casefold() in {"input.bin", "kernel.xclbin", "run.log",
                                          "host_app", "host_app.py"}):
        raise Kv260Error("output_name 必须是安全的单个文件名")
    if app_name and not _APP.fullmatch(app_name):
        raise Kv260Error("app_name 只能包含字母、数字、点、下划线和连字符")
    if app_name and not load_app:
        raise Kv260Error("app_name 仅在 load_app=true 时使用")
    if load_app and not app_name:
        raise Kv260Error("load_app=true 时必须提供 app_name")
    if unload_active and not load_app:
        raise Kv260Error("unload_active 仅能与 load_app=true 一起使用")

    session = Path(session_dir).expanduser().resolve()
    try:
        session_state = vision.session_status(str(session))
    except vision.VisionError as exc:
        raise Kv260Error(str(exc)) from exc
    if backend != "board":
        if host or ssh_key or app_name or load_app or unload_active or user != "ubuntu" or port != 22:
            raise Kv260Error(
                "离线 backend 不接受 host/SSH/xmutil 参数；只传 session 与离线运行参数"
            )
        return _offline_flow(
            session, session_state, backend, host_executable, xclbin,
            run_args, output_name, output_layout, stride_bytes, timeout, detail,
        )

    connection = _validate_connection(host, user, port, ssh_key)
    transport = _transport or _SshTransport(connection)
    executable, executable_profile = _host_artifact(host_executable)
    kernel = _local_artifact(xclbin, "xclbin", required=False)
    frame = session_state["frame"]
    tokens = _run_tokens(run_args, frame, kernel is not None, output_name)
    board_dir = session / "board"
    board_dir.mkdir(parents=True, exist_ok=True)
    log_path = board_dir / "board-flow.log"
    if _transport is None:
        transport.log_path = log_path

    inspection = inspect_board(transport, min(timeout, 120), detail)
    blocking = [
        failure for failure in inspection["failures"]
        if not (load_app and failure in {"xrt_examine_failed", "xrt_no_devices"})
    ]
    if blocking:
        raise Kv260Error(f"KV260 预检未通过: {inspection['failures']}")
    if load_app and not inspection["runtime"]["xmutil"]:
        installed = inspection["runtime"].get("xmutil_installed", False)
        reason = "xmutil 需要非交互 sudo" if installed else "板端没有 xmutil"
        raise Kv260Error(f"请求加载 Kria app，但{reason}")

    digest = hashlib.sha256(
        (str(session).casefold() + "|" + connection["host"].casefold()).encode("utf-8")
    ).hexdigest()[:12]
    safe_session = re.sub(r"[^A-Za-z0-9_.-]", "-", session.name)[:48] or "session"
    remote_dir = f"/tmp/xilinx-agent-bridge/{safe_session}-{digest}"
    quoted_dir = shlex.quote(remote_dir)
    transport.ssh(f"mkdir -p {quoted_dir}", timeout, label="create remote session")
    input_bin = _local_artifact(session_state["paths"]["input_bin"], "session input.bin")
    required_bytes = input_bin.stat().st_size + executable.stat().st_size
    if kernel is not None:
        required_bytes += kernel.stat().st_size
    required_mb = (required_bytes * 2 + (1024 * 1024 - 1)) // (1024 * 1024) + 16
    if inspection["free_mb"] and inspection["free_mb"] < required_mb:
        raise Kv260Error(
            f"KV260 /tmp 空间不足: free={inspection['free_mb']}MB required≈{required_mb}MB"
        )
    transport.upload(input_bin, f"{remote_dir}/input.bin", timeout)
    transport.upload(executable, f"{remote_dir}/host_app", timeout)
    if kernel is not None:
        transport.upload(kernel, f"{remote_dir}/kernel.xclbin", timeout)

    if load_app:
        if unload_active:
            _xmutil_checked(transport, "unloadapp", timeout)
        _xmutil_checked(transport, "loadapp", timeout, app_name)
        inspection = inspect_board(transport, min(timeout, 120), detail)
        if inspection["status"] != "ready":
            raise Kv260Error(f"加载 app 后 XRT 仍未就绪: {inspection['failures']}")

    quoted_args = " ".join(shlex.quote(value) for value in tokens)
    remote_timeout = max(5, timeout - 15)
    run_command = (
        f"cd {quoted_dir} && chmod u+x host_app && "
        f"timeout --signal=TERM --kill-after=5s {remote_timeout}s "
        f"./host_app {quoted_args} >run.log 2>&1; "
        "rc=$?; tail -n 60 run.log 2>/dev/null || true; "
        "printf '\\nXAB_KV260_RUN_RC|%s\\n' \"$rc\"; exit \"$rc\""
    )
    started = time.monotonic()
    run_result = transport.ssh(
        "sh -lc " + shlex.quote(run_command), timeout,
        check=False, label="run host application",
    )
    elapsed = round(time.monotonic() - started, 6)
    local_run_log = board_dir / "remote-run.log"
    transport.download(f"{remote_dir}/run.log", local_run_log, timeout, check=False)
    if run_result["returncode"] != 0:
        raise Kv260Error(
            f"KV260 host application 失败（rc={run_result['returncode']}）: "
            f"{_tail(run_result['stdout'] + run_result['stderr'])}；日志: {local_run_log}"
        )
    remote_output = remote_dir + "/" + output_name
    verified = transport.ssh(
        f"test -s {shlex.quote(remote_output)} && stat -c %s {shlex.quote(remote_output)}",
        timeout,
        label="verify board output",
    )
    try:
        remote_size = int(verified["stdout"].strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise Kv260Error("无法解析 KV260 输出文件大小") from exc
    if not 1 <= remote_size <= _MAX_BOARD_OUTPUT_BYTES:
        raise Kv260Error(
            f"KV260 输出大小必须在 1..{_MAX_BOARD_OUTPUT_BYTES} 字节，实际 {remote_size}"
        )
    suffix = Path(output_name).suffix or ".bin"
    local_output = board_dir / f"raw-output{suffix}"
    transport.download(remote_output, local_output, timeout)
    try:
        normalized = (
            str(local_output) if output_layout == "image"
            else vision.raw_frame_to_png(
                str(session), str(local_output), output_layout, stride_bytes
            )
        )
        evaluation = vision.evaluate_session(str(session), normalized)
    except vision.VisionError as exc:
        raise Kv260Error(str(exc)) from exc

    evidence = {
        "schema_version": 1, "completed": time.time(),
        "target": transport.target, "remote_dir": remote_dir,
        "app_name": app_name, "load_app": load_app,
        "host_executable": str(executable), "host_profile": executable_profile,
        "xclbin": str(kernel or ""),
        "run_args": tokens, "output_layout": output_layout,
        "stride_bytes": stride_bytes, "elapsed_seconds": elapsed,
        "local_raw_output": str(local_output), "normalized_output": normalized,
        "run_log": str(local_run_log), "evaluation": evaluation,
    }
    evidence_path = board_dir / "board-run.json"
    temp = evidence_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(evidence_path)

    result = {
        "status": evaluation["status"], "action": "run",
        "target": transport.target,
        "board": {
            "model": inspection["model"],
            "xrt_backend": inspection["runtime"]["xrt_backend"],
        },
        "run": {"returncode": 0, "elapsed_seconds": elapsed},
        "evaluation": vision.project_evaluation(evaluation, detail),
        "artifacts": {
            "output": normalized, "run_log": str(local_run_log),
            "evidence": str(evidence_path),
        },
        "next_action": (
            "硬件输出已通过黄金门禁" if evaluation["status"] == "pass"
            else "打开现有 KV260 review UI 人工确认" if evaluation["status"] == "review_required"
            else "查看差分图与板端日志，修复后重复一次 board run"
        ),
    }
    if detail == "standard":
        result["inspection"] = inspection
        result["remote_dir"] = remote_dir
        result["deployed"] = {
            "input_bin": str(input_bin), "host_executable": str(executable),
            "host_profile": executable_profile, "xclbin": str(kernel or ""),
            "raw_output": str(local_output),
        }
    return result
