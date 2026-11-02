"""全局配置：工具路径覆盖、默认参数、Job 根目录。配置文件在 ~/.xilinx-agent-bridge/config.json"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XAB_STATE_DIR") or (Path.home() / ".xilinx-agent-bridge")).expanduser().resolve()
CONFIG_FILE = CONFIG_DIR / "config.json"
JOBS_DIR = CONFIG_DIR / "jobs"

_DEFAULTS = {
    "vivado_path": "",        # 例如 C:/Xilinx/Vivado/2026.1
    "vitis_path": "",         # 例如 C:/Xilinx/Vitis/2026.1
    "hw_server_url": "localhost:3121",
    "default_jobs": 8,        # synth/impl 并行 job 数
    "license_file": "",       # XILINXD_LICENSE_FILE / LM_LICENSE_FILE 覆盖
    "vivado_launcher": "auto",  # auto / wrapper / unwrapped (Windows)
    "selected_version": "",   # 例如 2022.2；由 core 解析为确切安装路径
}


def _load_file() -> dict:
    cfg = dict(_DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def load() -> dict:
    cfg = _load_file()
    # 环境变量优先级最高
    if os.environ.get("XAB_VIVADO_PATH"):
        cfg["vivado_path"] = os.environ["XAB_VIVADO_PATH"]
    if os.environ.get("XAB_VITIS_PATH"):
        cfg["vitis_path"] = os.environ["XAB_VITIS_PATH"]
    if os.environ.get("XAB_LICENSE_FILE"):
        cfg["license_file"] = os.environ["XAB_LICENSE_FILE"]
    if os.environ.get("XAB_VIVADO_LAUNCHER"):
        cfg["vivado_launcher"] = os.environ["XAB_VIVADO_LAUNCHER"].casefold()
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    merged = dict(_DEFAULTS)
    merged.update(cfg)
    CONFIG_FILE.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def set_paths(vivado_path: str = "", vitis_path: str = "",
              license_file: str = "", vivado_launcher: str = "",
              reset: bool = False, selected_version: str = "") -> dict:
    # Read only persisted values here. Environment overrides must never become
    # permanent merely because another setting was changed.
    cfg = _load_file()
    if reset:
        cfg = dict(_DEFAULTS)
    if vivado_path:
        cfg["vivado_path"] = vivado_path.replace("\\", "/")
    if vitis_path:
        cfg["vitis_path"] = vitis_path.replace("\\", "/")
    if license_file:
        cfg["license_file"] = license_file.replace("\\", "/")
    if vivado_launcher:
        cfg["vivado_launcher"] = vivado_launcher.casefold()
    if (vivado_path or vitis_path) and not selected_version:
        cfg["selected_version"] = ""
    if selected_version:
        cfg["selected_version"] = selected_version
        # Version selection is atomic: do not retain a Vitis path from another
        # release when that release has no separately installed Vitis root.
        cfg["vivado_path"] = vivado_path.replace("\\", "/")
        cfg["vitis_path"] = vitis_path.replace("\\", "/")
    save(cfg)
    return cfg
