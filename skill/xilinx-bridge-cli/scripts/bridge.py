"""Standalone entry point for the bundled Xilinx Agent Bridge."""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from pathlib import Path
from types import UnionType
from typing import Literal, get_args, get_origin, get_type_hints


def matches(value, annotation):
    """Check JSON types before a value reaches a hardware/configuration action."""
    kind = get_origin(annotation)
    args = get_args(annotation)
    if kind is UnionType:
        return any(matches(value, item) for item in args)
    if kind is Literal:
        return value in args and any(type(value) is type(item) for item in args)
    if kind is list:
        return isinstance(value, list) and all(matches(item, args[0]) for item in value)
    if kind is dict:
        return isinstance(value, dict) and all(matches(k, args[0]) and matches(v, args[1]) for k, v in value.items())
    if annotation is float:
        return type(value) in (int, float)
    return type(value) is annotation


def local_tools(argv, board_profile=None):
    from xilinx_bridge.cli import failed_result
    from xilinx_bridge.handlers import TOOLS

    parser = argparse.ArgumentParser(prog="bridge.py " + argv[0])
    if argv[0] != "tools":
        parser.add_argument("tool", choices=sorted(TOOLS))
    if argv[0] == "call":
        parser.add_argument("--file", help="UTF-8 JSON object, or '-' for standard input; default {}.")
    args = parser.parse_args(argv[1:])
    if argv[0] == "tools":
        print("\n".join(sorted(TOOLS)))
        return 0
    handler = TOOLS[args.tool]
    signature = inspect.signature(handler)
    if argv[0] == "describe":
        print(f"{args.tool}{signature}\n\n{inspect.getdoc(handler)}")
        return 0
    try:
        payload = {}
        if args.file:
            source = sys.stdin.buffer.read() if args.file == "-" else Path(args.file).read_bytes()
            payload = json.loads(source.decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("arguments must be a JSON object")
        if board_profile is not None:
            payload = board_profile.parameters(args.tool, payload)
        bound = signature.bind(**payload)
        hints = get_type_hints(handler)
        for key, value in bound.arguments.items():
            if value is None and signature.parameters[key].default is None:
                continue
            if not matches(value, hints[key]):
                raise ValueError(f"{key}: expected {signature.parameters[key].annotation}")
        if "wait" in signature.parameters:
            if payload.get("wait") is False:
                raise ValueError("foreground CLI requires wait=true; use 'serve' for managed asynchronous jobs")
            payload["wait"] = True
        if board_profile is not None and args.tool == "kv260_board_flow":
            identity = board_profile.inspect_linux()
            reported = " ".join([identity["reported_model"], *identity["compatible"]])
            if "kv260" not in reported.lower():
                raise ValueError("The SSH target device tree did not identify a KV260 carrier")
        result = json.loads(handler(**payload))
    except (TypeError, ValueError, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(failed_result(result))


def main(argv):
    scripts = str(Path(__file__).resolve().parent)
    sys.dont_write_bytecode = True
    sys.path.insert(0, scripts)
    # Helpers for vision review / Vitis AI spawn a new interpreter in the user's workspace.
    inherited = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = scripts + (os.pathsep + inherited if inherited else "")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    profile = None
    if argv[:1] == ["--board-profile"]:
        from xilinx_bridge.board_profile import BoardProfile
        if len(argv) < 3:
            print("Usage: bridge.py --board-profile FILE COMMAND ...", file=sys.stderr)
            return 2
        try:
            profile = BoardProfile(argv[1])
        except (ValueError, OSError) as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 2
        argv = argv[2:]
    if argv[:1] in (["tools"], ["describe"], ["call"]):
        return local_tools(argv, profile)
    if argv[:1] in (["-h"], ["--help"]):
        print("Board selection: --board-profile FILE COMMAND ...\n"
              "Agent tools: tools | describe TOOL | call TOOL [--file FILE|-]\nBridge commands:", flush=True)
    if argv[:1] == ["build"] and "--wait" not in argv:
        argv = [*argv, "--wait"]
    if argv[:1] == ["vitis-ai"] and "--no-wait" in argv:
        print("Use 'serve' for asynchronous work, or omit --no-wait.", file=sys.stderr)
        return 2
    from xilinx_bridge.cli import main as bridge_cli
    try:
        return bridge_cli(argv, board_profile=profile)
    except ModuleNotFoundError as exc:
        if exc.name == "fastmcp" and argv[:1] == ["serve"]:
            print("Install fastmcp>=2 in this Python environment to use the optional MCP transport.", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
