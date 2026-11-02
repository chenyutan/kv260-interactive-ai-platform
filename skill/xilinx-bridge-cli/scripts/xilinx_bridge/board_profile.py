"""Select an FPGA target per invocation. Host-side credentials stay outside the skill."""
from __future__ import annotations

import json
import re
from pathlib import Path
from xml.etree import ElementTree

from . import kv260


def _fields(obj, allowed, where):
    if not isinstance(obj, dict):
        raise ValueError(f"{where} must be a JSON object")
    extra = obj.keys() - allowed
    if extra:
        raise ValueError(f"Unknown {where} keys: {', '.join(sorted(extra))}")
    return obj


def _merge(arguments, expected):
    result = arguments.copy()
    for key, value in expected.items():
        if key in result and result[key] != value:
            raise ValueError(f"Request {key} conflicts with the selected board profile")
        result[key] = value
    return result


class BoardProfile:
    def __init__(self, filename):
        path = Path(filename).expanduser().resolve()
        data = _fields(json.loads(path.read_text(encoding="utf-8-sig")),
                       {"name", "part", "adapter", "jtag", "ssh", "dpu_arch"}, "board profile")
        self.name = data.get("name")
        self.part = data.get("part")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Board profile requires a name")
        if not isinstance(self.part, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]+", self.part):
            raise ValueError("Board profile requires the complete Vivado part")
        self.adapter = data.get("adapter", "generic")
        if self.adapter not in ("generic", "kv260"):
            raise ValueError("adapter must be generic or kv260")
        if self.adapter == "kv260" and self.part.lower() != "xck26-sfvc784-2lv-c":
            raise ValueError("The kv260 adapter requires the KV260 Vivado part")
        self.dpu_arch = None
        if "dpu_arch" in data:
            if not isinstance(data["dpu_arch"], str) or not data["dpu_arch"].strip():
                raise ValueError("dpu_arch must name the architecture JSON for this DPU design")
            self.dpu_arch = str((path.parent / Path(data["dpu_arch"]).expanduser()).resolve())
        self.jtag = _fields(data.get("jtag", {}),
                            {"server", "target_name", "device_name", "device_part"}, "jtag")
        for key, value in self.jtag.items():
            if not isinstance(value, str) or not value.strip() or not value.isprintable():
                raise ValueError(f"jtag.{key} must be a nonempty printable string")
        if "device_part" in self.jtag:
            chip = self.jtag["device_part"].lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9.-]+", chip) or not self.part.lower().startswith(chip):
                raise ValueError("jtag.device_part must agree with the chip in the complete Vivado part")
        self.ssh = None
        if "ssh" in data:
            ssh = _fields(data["ssh"], {"host", "user", "port", "ssh_key"}, "ssh")
            key = ssh.get("ssh_key", "")
            if not isinstance(key, str):
                raise ValueError("ssh.ssh_key must be a local path")
            if key:
                key = str(path.parent / Path(key).expanduser())
            self.ssh = kv260._validate_connection(ssh.get("host"), ssh.get("user"),
                                                  ssh.get("port", 22), key)

    def project(self, xpr):
        if not isinstance(xpr, str) or not xpr.strip():
            raise ValueError("xpr must name a Vivado project file")
        path = Path(xpr).expanduser().resolve()
        try:
            options = ElementTree.parse(path).getroot()
        except ElementTree.ParseError as exc:
            raise ValueError(f"Cannot read Vivado project part: {xpr}") from exc
        option = options.find("./Configuration/Option[@Name='Part']")
        if option is None or option.get("Val", "").lower() != self.part.lower():
            raise ValueError(f"Project {xpr} does not target {self.part}")
        for run in options.findall("./Runs/Run"):
            if run.get("Id") in ("synth_1", "impl_1") and run.get("Part"):
                if run.get("Part").lower() != self.part.lower():
                    raise ValueError(f"{run.get('Id')} in {xpr} does not target {self.part}")
        return str(path)

    def parameters(self, tool, args):
        if tool == "vivado_create_project":
            if "part" in args and isinstance(args["part"], str) and args["part"].lower() == self.part.lower():
                args = {**args, "part": self.part}
            return _merge(args, {"part": self.part})
        if tool.startswith("vivado_") and "xpr" in args:
            return {**args, "xpr": self.project(args["xpr"])}
        if tool == "vitis_build" and isinstance(args.get("hls"), dict):
            return {**args, "hls": self.parameters("vivado_create_project", args["hls"])}
        if tool == "vitis_ai_flow" and args.get("action") in ("compile", "pipeline"):
            if not self.dpu_arch:
                raise ValueError("Set dpu_arch to the architecture JSON of the installed DPU")
            return _merge(args, {"arch_json": self.dpu_arch})
        if tool in ("hw_list_devices", "hw_program_fpga", "hw_program_flash", "hw_ila_capture"):
            if "server" not in self.jtag:
                raise ValueError("Set jtag.server in the board profile")
            if tool == "hw_list_devices":
                return _merge(args, {"server": self.jtag["server"]})
            if not all(k in self.jtag for k in ("target_name", "device_name", "device_part")):
                raise ValueError("Scan with devices; bind the exact target_name, device_name and device_part")
            if "target_index" in args or "device_index" in args:
                raise ValueError("Profiled hardware operations use exact names; omit target_index and device_index")
            return _merge(args, self.jtag)
        if tool == "kv260_board_flow":
            if self.adapter != "kv260":
                raise ValueError("This tool requires the kv260 board adapter")
            if args.get("backend", "board") != "board" or args.get("action", "inspect") == "host_inspect":
                raise ValueError("Use the unprofiled CLI for host inspection and offline backends")
            if not self.ssh:
                raise ValueError("Set ssh.host and ssh.user for this board")
            return _merge(args, self.ssh)
        raise ValueError(f"{tool} has no board-profile binding. Use it without --board-profile.")

    def inspect_linux(self):
        if not self.ssh:
            raise ValueError("board-inspect requires a Linux target with ssh.host and ssh.user")
        transport = kv260._SshTransport(self.ssh)
        command = (
            "set -e; uname -s; uname -r; uname -m; "
            "if [ -r /proc/device-tree/model ]; then tr -d '\\000' < /proc/device-tree/model; fi; printf '\\n'; "
            "if [ -r /proc/device-tree/compatible ]; then tr '\\000' '\\n' < /proc/device-tree/compatible; fi"
        )
        reply = transport.ssh(command, 20, check=False)
        if reply["returncode"] != 0:
            raise ValueError(f"SSH inspection failed: {reply['stderr'].strip()}")
        lines = reply["stdout"].splitlines()
        if len(lines) < 3 or lines[0] != "Linux":
            raise ValueError("SSH target did not return a Linux system identity")
        return {"status": "success", "profile": self.name, "configured_part": self.part,
                "host": self.ssh["host"], "kernel": lines[1], "machine": lines[2],
                "reported_model": lines[3] if len(lines) > 3 else "", "compatible": lines[4:]}
