"""Configured cross-process EDA capacity budgets, not a license-server probe."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import config


def requirements(kind: str, command: list[str], metadata: dict) -> list[tuple[str, int]]:
    """Keep disabled by default; an operator declares real available seat budgets."""
    raw = os.environ.get("XAB_LICENSE_LIMITS")
    if raw is not None:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate license budget class")
                result[key] = value
            return result
        try:
            limits = json.loads(raw, object_pairs_hook=unique)
        except (ValueError, TypeError) as exc:
            raise ValueError("XAB_LICENSE_LIMITS must be a JSON object of integer budgets") from exc
    else:
        limits = config.load().get("license_limits", {})
    if not isinstance(limits, dict) or set(limits) - {"global", "vivado", "vitis", "hls"}:
        raise ValueError("license budgets support only global/vivado/vitis/hls")
    if any(type(value) is not int or not 1 <= value <= 128 for value in limits.values()):
        raise ValueError("license budgets must be integers in 1..128")
    explicit = metadata.get("license_class")
    if explicit is not None and explicit not in {"vivado", "vitis", "hls"}:
        raise ValueError("invalid license_class")
    executable = Path(str(command[0]).replace("\\", "/")).name.casefold() if command else ""
    group = explicit
    if group is None:
        if kind == "vitis_hls" or executable.startswith(("vitis_hls", "vitis-run")):
            group = "hls"
        elif executable in {"vivado", "vivado.bat", "vivado.exe"}:
            group = "vivado"
        elif executable in {"vitis", "vitis.bat", "vitis.exe", "xsct", "xsct.bat"}:
            group = "vitis"
    if group is None:
        return []  # Unrelated Python, SSH or hardware discovery must not be serialized.
    return [(name, limits[name]) for name in (group, "global") if name in limits]
