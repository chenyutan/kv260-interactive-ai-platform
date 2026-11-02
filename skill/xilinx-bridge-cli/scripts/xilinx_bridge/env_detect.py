"""自动探测本机 Vivado / Vitis 安装。

搜索顺序:
1. 用户配置 / 环境变量覆盖 (config.py)
2. XILINX_VIVADO / XILINX_VITIS 环境变量
3. 常见安装根目录扫描 (Windows: C:/Xilinx, D:/Xilinx, C:/AMD; Linux: /tools/Xilinx, /opt/Xilinx)
4. PATH 上的可执行文件
"""
from __future__ import annotations

import copy
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

from . import config

IS_WIN = sys.platform.startswith("win")
EXE = ".bat" if IS_WIN else ""

_SEARCH_ROOTS = (
    ["C:/Xilinx", "D:/Xilinx", "E:/Xilinx", "C:/AMD", "C:/design_tools"]
    if IS_WIN
    else ["/tools/Xilinx", "/opt/Xilinx", "/xilinx", str(Path.home() / "Xilinx")]
)

_CACHE_TTL_S = 60.0
_CACHE_LOCK = threading.Lock()
_CACHE_KEY: tuple | None = None
_CACHE_AT = 0.0
_CACHE_VALUE: dict | None = None

_LICENSE_ENV_NAMES = (
    "XILINXD_LICENSE_FILE", "LM_LICENSE_FILE", "RDI_LICENSE_FILE",
)


def _search_roots() -> list[str]:
    """默认根目录 + 各盘符下一层的 xilinx/amd 命名目录(如 E:/xilinx2026.1)。"""
    roots = list(_SEARCH_ROOTS)
    if IS_WIN:
        for drive in "CDEFGH":
            d = Path(f"{drive}:/")
            if not d.is_dir():
                continue
            try:
                for cand in d.iterdir():
                    if cand.is_dir() and re.match(r"(?i)(xilinx|amd)", cand.name):
                        p = str(cand).replace("\\", "/")
                        if p not in roots:
                            roots.append(p)
            except PermissionError:
                continue
    return roots


def _exe(base: Path, name: str) -> Path | None:
    """在 <base>/bin 下查找可执行文件（Windows 优先 .bat/.cmd）。"""
    bindir = base / "bin"
    for cand in (
        [bindir / f"{name}.bat", bindir / f"{name}.cmd", bindir / f"{name}.exe", bindir / name]
        if IS_WIN
        else [bindir / name]
    ):
        if cand.exists():
            return cand
    return None


def _unwrapped_exe(root: Path, name: str = "vivado") -> Path | None:
    if not IS_WIN:
        return None
    candidate = root / "bin" / "unwrapped" / "win64.o" / f"{name}.exe"
    return candidate if candidate.is_file() else None


def _version_of(root: Path) -> str:
    m = re.match(r"(\d{4}\.\d+)", root.name)
    if m:
        return m.group(1)
    # 形如 .../2026.1/Vivado 的统一安装布局: 看父目录名
    m = re.match(r"(\d{4}\.\d+)", root.parent.name)
    return m.group(1) if m else ""


def _version_key(version: str) -> tuple[int, ...]:
    """按数字段比较版本，避免 2026.10 被排在 2026.2 之后。"""
    nums = re.findall(r"\d+", version)
    return tuple(int(n) for n in nums) if nums else (0,)


def installations_for_version(version: str) -> dict[str, str]:
    """Resolve an exact installed release without starting Vivado or Vitis."""
    selected: dict[str, str] = {}
    for key, product in (("vivado_path", "Vivado"), ("vitis_path", "Vitis")):
        match = next(
            (entry for entry in _scan_product(product) if entry["version"] == version),
            None,
        )
        if match:
            selected[key] = match["root"]
    return selected


def _scan_product(product: str) -> list[dict]:
    """扫描某个产品线 (Vivado / Vitis) 的所有安装。"""
    found: list[dict] = []
    seen = set()

    def add(root: Path):
        root = root.resolve()
        if str(root) in seen or not root.is_dir():
            return
        exe_name = product.lower()
        exe = _exe(root, exe_name)
        if exe is None and product.lower() == "vitis":
            exe = _exe(root, "vitis") or _exe(root, "vitis_hls")
        if exe is None:
            return
        seen.add(str(root))
        found.append(
            {
                "version": _version_of(root),
                "root": str(root).replace("\\", "/"),
                "launcher": str(exe).replace("\\", "/"),
            }
        )

    env_keys = {
        "vivado": ["XILINX_VIVADO", "XILINX_VIVADO_HLS"],
        "vitis": ["XILINX_VITIS", "XILINX_HLS"],
        "vitis_hls": ["XILINX_HLS", "XILINX_VITIS"],
    }[product.lower()]
    for k in env_keys:
        v = os.environ.get(k)
        if v:
            add(Path(v))

    for base in _search_roots():
        b = Path(base)
        if not b.is_dir():
            continue
        # 结构: <base>/Vivado/2026.1 或 <base>/2026.1/Vivado (新版统一安装)
        for child in (b / product).glob("*") if (b / product).is_dir() else []:
            add(child)
        for ver_dir in b.glob("20*"):
            cand = ver_dir / product
            if cand.is_dir():
                add(cand)
        # 单层直接是产品目录的情况, 如 C:/Xilinx/Vivado 本身无版本子目录时略过

    # PATH 兜底
    on_path = shutil.which(product.lower())
    if on_path:
        p = Path(on_path)
        # .../bin/vivado -> root 是 bin 的上一级
        if p.parent.name.lower() == "bin":
            add(p.parent.parent)

    found.sort(key=lambda d: _version_key(d["version"]), reverse=True)
    return found


def _detect_uncached(cfg: dict) -> dict:
    result = {
        "vivado": _scan_product("Vivado"),
        "vitis": _scan_product("Vitis"),
        "hls": _scan_product("Vitis_HLS"),
        "warnings": [],
    }

    # 应用用户覆盖（放到最前）
    for key, product in (("vivado_path", "vivado"), ("vitis_path", "vitis")):
        override = cfg.get(key)
        if not override:
            continue
        override_path = Path(override).expanduser()
        if override_path.is_dir():
            root = override_path.resolve()
            launcher = _exe(root, product)
            if launcher is None:
                result["warnings"].append(
                    f"配置的 {product} 目录存在，但未找到启动器: {root}"
                )
                continue
            entry = {
                "version": _version_of(root),
                "root": str(root).replace("\\", "/"),
                "launcher": str(launcher).replace("\\", "/"),
                "user_configured": True,
            }
            root_key = entry["root"].casefold()
            result[product] = [
                e for e in result[product] if e["root"].casefold() != root_key
            ]
            result[product].insert(0, entry)
        else:
            result["warnings"].append(
                f"配置的 {product} 目录不存在: {override}"
            )
    return result


def _detect_cache_key(cfg: dict) -> tuple:
    env_names = (
        "XILINX_VIVADO", "XILINX_VIVADO_HLS", "XILINX_VITIS", "XILINX_HLS", "PATH",
    )
    return (
        cfg.get("vivado_path", ""),
        cfg.get("vitis_path", ""),
        cfg.get("vivado_launcher", "auto"),
        cfg.get("selected_version", ""),
        *(os.environ.get(name, "") for name in env_names),
    )


def invalidate_cache() -> None:
    global _CACHE_KEY, _CACHE_AT, _CACHE_VALUE
    with _CACHE_LOCK:
        _CACHE_KEY = None
        _CACHE_AT = 0.0
        _CACHE_VALUE = None


def _default_license_dirs() -> list[Path]:
    """返回 Vivado/FlexNet 常用的用户许可证目录；只做常数级目录检查。"""
    dirs: list[Path] = []
    if IS_WIN:
        appdata = os.environ.get("APPDATA")
        if appdata:
            dirs.append(Path(appdata) / "XilinxLicense")
    dirs.extend((Path.home() / ".Xilinx", Path.home() / "XilinxLicense"))
    # 保序去重，避免 APPDATA 与 home 推导路径重叠。
    return list(dict.fromkeys(dirs))


def license_info() -> dict:
    """报告可见的许可证来源，不把“未显式覆盖”误判成“没有许可证”。

    这里只做快速的配置/环境/默认目录检查，不启动 Vivado。许可证是否有效、
    覆盖哪些 feature，仍以实际 Vivado 调用结果为准。
    """
    cfg = config.load()
    if cfg.get("license_file"):
        source = "bridge_override"
        candidate_found = True
    elif any(os.environ.get(name) for name in _LICENSE_ENV_NAMES):
        source = "environment"
        candidate_found = True
    else:
        candidate_found = False
        source = "implicit_or_none"
        for directory in _default_license_dirs():
            try:
                if directory.is_dir() and any(directory.glob("*.lic")):
                    source = "default_directory"
                    candidate_found = True
                    break
            except OSError:
                continue
    return {
        "source": source,
        "candidate_found": candidate_found,
        "validation": "not_probed",
    }


def detect(refresh: bool = False) -> dict:
    """完整探测；同一 MCP 进程内缓存 60 秒，配置或环境变化会自动失效。"""
    global _CACHE_KEY, _CACHE_AT, _CACHE_VALUE
    cfg = config.load()
    key = _detect_cache_key(cfg)
    now = time.monotonic()
    with _CACHE_LOCK:
        if (
            not refresh and _CACHE_VALUE is not None and _CACHE_KEY == key
            and now - _CACHE_AT < _CACHE_TTL_S
        ):
            return copy.deepcopy(_CACHE_VALUE)
        value = _detect_uncached(cfg)
        _CACHE_KEY = key
        _CACHE_AT = now
        _CACHE_VALUE = value
        return copy.deepcopy(value)


def resolve_tool(tool: str, detected: dict | None = None) -> Path | None:
    """解析具体可执行文件，包括跨版本 xsdb/xsct 调试后端。"""
    det = detected or detect()
    vivado_root = Path(det["vivado"][0]["root"]) if det["vivado"] else None
    vitis_root = Path(det["vitis"][0]["root"]) if det["vitis"] else None
    hls_root = Path(det.get("hls", [])[0]["root"]) if det.get("hls") else None

    if tool in ("xsdb", "xsct"):
        for root in (vitis_root, vivado_root):
            if root and (e := _exe(root, tool)):
                return e
        return None
    if tool in ("vivado", "hw_server", "updatemem", "bootgen"):
        for root in (vivado_root, vitis_root):
            if root and (e := _exe(root, tool)):
                return e
        return None
    if tool in ("vitis", "program_flash"):
        for root in (vitis_root, vivado_root):
            if root and (e := _exe(root, tool)):
                return e
        return None
    if tool in ("vitis_run", "vpp", "vitis_hls"):
        executable = {"vitis_run": "vitis-run", "vpp": "v++", "vitis_hls": "vitis_hls"}[tool]
        for root in (vitis_root, hls_root, vivado_root):
            if root and (e := _exe(root, executable)):
                return e
        return None
    return None


def _newest_child(parent: Path, pattern: str) -> Path | None:
    try:
        candidates = [path for path in parent.glob(pattern) if path.is_dir()]
    except OSError:
        return None
    return max(candidates, key=lambda path: _version_key(path.name), default=None)


def _vivado_unwrapped_env(root: Path) -> dict[str, str]:
    """Build the runtime environment required by the Windows unwrapped loader."""
    root = root.resolve()
    tps_win = root / "tps" / "win64"
    python_root = _newest_child(tps_win, "python-*")
    jre_root = _newest_child(tps_win, "jre*")
    tcl_root = _newest_child(root / "tps" / "tcl", "tcl*")
    path_entries = [
        root / "bin" / "unwrapped" / "win64.o",
        root / "bin", root / "lib" / "win64.o", tps_win,
    ]
    if python_root:
        path_entries.extend((python_root, python_root / "bin", python_root / "lib"))
    if jre_root:
        path_entries.extend((jre_root / "bin" / "server", jre_root / "bin"))
    path_entries.extend((
        root / "ids_lite" / "ISE" / "bin" / "nt64",
        root / "ids_lite" / "ISE" / "lib" / "nt64",
    ))
    try:
        mingw_bins = list((root / "tps" / "mingw").glob("*/win64.o/nt/bin"))
        mingw_libexec = list((root / "tps" / "mingw").glob("*/win64.o/nt/libexec/gcc/*/*"))
    except OSError:
        mingw_bins, mingw_libexec = [], []
    path_entries.extend(mingw_bins + mingw_libexec)
    current_path = os.environ.get("PATH", "")
    env = {
        "RDI_APPROOT": str(root), "HDI_APPROOT": str(root),
        "RDI_BASEROOT": str(root.parent), "RDI_BINROOT": str(root / "bin"),
        "RDI_DATADIR": str(root / "data"), "RDI_LIBDIR": str(root / "lib" / "win64.o"),
        "RDI_BINDIR": str(root / "bin"), "RDI_TPS_ROOT": str(tps_win),
        "RDI_OPT_EXT": ".o", "XILINX_VIVADO": str(root),
        "XILINX": str(root / "ids_lite" / "ISE"), "RDI_PLATFORM": "win64",
        "RT_LIBPATH": str(root / "scripts" / "rt" / "data"),
        "RT_TCL_PATH": str(root / "scripts" / "rt" / "base_tcl" / "tcl"),
        "SYNTH_COMMON": str(root / "scripts" / "rt" / "data"),
        "ISL_IOSTREAMS_RSA": str(root / "tps" / "isl"), "RDI_BUILD": "yes",
        "RDI_USE_JDK11": "True", "_RDI_NEEDS_PYTHON": "True", "Malloc": "1",
        "PATH": os.pathsep.join(str(path) for path in path_entries if path.is_dir())
                + (os.pathsep + current_path if current_path else ""),
    }
    if tcl_root:
        env["TCL_LIBRARY"] = str(tcl_root)
    if python_root:
        env["RDI_PYTHON3"] = str(python_root)
        env["PYTHONHOME"] = str(python_root)
        env["PYTHONPATH"] = os.pathsep.join(str(path) for path in (
            python_root, python_root / "bin", python_root / "lib",
            python_root / "lib" / "site-packages",
        ))
    return env


def resolve_vivado_invocation(detected: dict | None = None) -> tuple[Path | None, dict, str]:
    """Resolve Vivado launcher plus optional unwrapped runtime environment."""
    det = detected or detect()
    if not det.get("vivado"):
        return None, {}, "unavailable"
    root = Path(det["vivado"][0]["root"])
    mode = str(config.load().get("vivado_launcher", "auto")).casefold()
    if mode not in {"auto", "wrapper", "unwrapped"}:
        raise ValueError("vivado_launcher 必须是 auto、wrapper 或 unwrapped")
    if mode == "unwrapped":
        executable = _unwrapped_exe(root)
        if executable is None:
            raise ValueError(f"当前 Vivado 安装没有 unwrapped 启动器: {root}")
        return executable, _vivado_unwrapped_env(root), "unwrapped"
    return _exe(root, "vivado"), {}, "wrapper"


def tool_info(refresh: bool = False, detail: str = "compact") -> dict:
    """供 MCP 工具返回的简洁信息。"""
    det = detect(refresh=refresh)
    license_state = license_info()
    out = {
        "vivado": det["vivado"][0] if det["vivado"] else None,
        "vitis": det["vitis"][0] if det["vitis"] else None,
        "all_vivado": det["vivado"],
        "all_vitis": det["vitis"],
        "hls": det.get("hls", [None])[0] if det.get("hls") else None,
        "all_hls": det.get("hls", []),
        "executables": {},
        "warnings": det.get("warnings", []),
        "license": license_state,
        # 0.3.x 兼容字段：表示发现候选来源，不代表许可证已通过有效性校验。
        "license_configured": license_state["candidate_found"],
    }
    for t in ("vivado", "vitis", "vitis_run", "vpp", "vitis_hls", "xsdb", "xsct", "hw_server", "program_flash", "bootgen", "updatemem"):
        p = resolve_tool(t, det)
        out["executables"][t] = str(p).replace("\\", "/") if p else None
    xsdb = out["executables"]["xsdb"]
    xsct = out["executables"]["xsct"]
    recommended = "xsdb" if xsdb else "xsct" if xsct else None
    out["debug_backend"] = {
        "recommended": recommended,
        "xsdb_available": bool(xsdb),
        "xsct_available": bool(xsct),
        "reason": (
            "优先使用当前 Vitis 随附的 XSDB；缺失时兼容 XSCT"
            if xsdb else "当前安装仅检测到 XSCT" if xsct else "未检测到调试后端"
        ),
    }
    hints = list(out["warnings"])
    if not det["vivado"]:
        hints.append("未检测到 Vivado，可调用 xilinx_config_set 手动指定安装路径")
    if not det["vitis"]:
        hints.append("未检测到 Vitis，可调用 xilinx_config_set 手动指定安装路径")
    out["hint"] = "；".join(hints)
    vivado_root = Path(out["vivado"]["root"]) if out["vivado"] else None
    launcher_mode = str(config.load().get("vivado_launcher", "auto")).casefold()
    out["vivado_launcher"] = {
        "configured": launcher_mode,
        "selected": "unwrapped" if launcher_mode == "unwrapped" else "wrapper",
        "unwrapped_available": bool(vivado_root and _unwrapped_exe(vivado_root)),
    }
    if detail == "standard":
        return out
    if detail != "compact":
        raise ValueError("detail 必须是 compact 或 standard")

    def selected(entry: dict | None) -> dict | None:
        if not entry:
            return None
        return {key: entry[key] for key in ("version", "root", "user_configured") if key in entry}

    return {
        "vivado": selected(out["vivado"]), "vitis": selected(out["vitis"]),
        "hls": selected(out["hls"]),
        "capabilities": {name: bool(path) for name, path in out["executables"].items()},
        "debug_backend": out["debug_backend"], "vivado_launcher": out["vivado_launcher"],
        "warnings": out["warnings"], "license": out["license"],
        "license_configured": out["license_configured"], "hint": out["hint"],
    }
