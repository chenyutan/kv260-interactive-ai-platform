"""Fast, bounded Vitis workspace audit for copied or migrated projects.

The audit is deliberately local: it does not launch Vitis and it never returns
source bodies.  It looks for the high-value failure modes seen in real agent
workflows: a copied app still pointing at the original XPFM/XSA/BSP, a missing
compile database, and a real-time application accidentally left at ``-O0``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


_SKIP_DIRS = {
    ".git", ".pytest_cache", "__pycache__", ".cache", "node_modules",
    ".venv", "venv", "site-packages",
}
_GENERATED_DIRS = {"cmakefiles", "build_configs", ".metadata", ".xil"}
_METADATA_NAMES = {
    "cmakelists.txt", "makefile", "platform.json", "component.xml",
    ".project", ".cproject", "compile_commands.json", "userconfig.cmake",
}
_METADATA_SUFFIXES = {
    ".xpfm", ".spr", ".json", ".yaml", ".yml", ".cmake", ".mk",
    ".launch", ".ini",
}
_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
_WINDOWS_QUOTED_PATH = re.compile(r"[\"']([A-Za-z]:[\\/][^\"'\r\n]+)[\"']")
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]:[\\/][^\s\"'<>|]+)")
_POSIX_QUOTED_PATH = re.compile(r"[\"'](/[^\"'\r\n]+)[\"']")
_HIGH_VALUE_EXTENSIONS = {
    ".xsa": "xsa", ".xpfm": "xpfm", ".elf": "elf", ".bit": "bit",
    ".a": "library",
}


def _display(path: Path) -> str:
    return str(path).replace("\\", "/")


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _looks_like_toolchain(path_text: str) -> bool:
    lowered = path_text.replace("\\", "/").casefold()
    if any(token in lowered for token in (
        "/xilinx/vivado/", "/xilinx/vitis/", "/amd/vivado/", "/amd/vitis/",
        "/gnu/", "/llvm/", "/clang/",
    )):
        return True
    return bool(re.search(
        r"/(?:xilinx|amd)[^/]*/(?:\d{4}\.\d+/)?(?:vivado|vitis|data|tps)/",
        lowered,
    ))


def _clean_path(value: str) -> str:
    value = value.replace("\\\\", "\\").strip()
    return value.rstrip(",;)]}")


def _candidate_files(root: Path, max_files: int) -> tuple[list[Path], bool, bool]:
    selected: list[Path] = []
    has_sources = False
    truncated = False
    for directory, dirs, files in os.walk(root, onerror=lambda _exc: None):
        dirs[:] = [name for name in dirs if name.casefold() not in _SKIP_DIRS]
        base = Path(directory)
        try:
            rel_parts = {part.casefold() for part in base.relative_to(root).parts}
        except ValueError:
            rel_parts = set()
        generated = bool(rel_parts & _GENERATED_DIRS)
        for name in files:
            path = base / name
            suffix = path.suffix.casefold()
            if suffix in _SOURCE_SUFFIXES:
                has_sources = True
            is_compile_db = name.casefold() == "compile_commands.json"
            if is_compile_db or (
                not generated and (name.casefold() in _METADATA_NAMES or suffix in _METADATA_SUFFIXES)
            ):
                if len(selected) >= max_files:
                    # Compile databases are high-value evidence even if a large
                    # generated platform exhausted the normal metadata budget.
                    if not is_compile_db or sum(
                        path.name.casefold() == "compile_commands.json" for path in selected
                    ) >= 8:
                        truncated = True
                        continue
                try:
                    if path.stat().st_size <= 2 * 1024 * 1024:
                        selected.append(path)
                except OSError:
                    continue
    return selected, has_sources, truncated


def _extract_paths(text: str) -> list[tuple[str, str]]:
    """Return unique absolute paths and a short surrounding context."""
    found: dict[str, str] = {}
    patterns = (_WINDOWS_QUOTED_PATH, _WINDOWS_PATH, _POSIX_QUOTED_PATH)
    for pattern in patterns:
        for match in pattern.finditer(text):
            value = _clean_path(match.group(1))
            if len(value) > 1024:
                continue
            context = text[max(0, match.start() - 80):min(len(text), match.end() + 80)]
            found.setdefault(value, context)
    return list(found.items())


def _reference_kind(path_text: str, context: str) -> str:
    suffix = Path(path_text).suffix.casefold()
    if suffix in _HIGH_VALUE_EXTENSIONS:
        return _HIGH_VALUE_EXTENSIONS[suffix]
    lowered = context.casefold()
    for token in ("platform", "workspace", "bsp", "sysroot", "hw_design"):
        if token in lowered:
            return token
    return "external"


def audit_workspace(workspace: str | Path, *, max_files: int = 240,
                    max_issues: int = 12) -> dict:
    """Audit workspace metadata without starting Vitis or reading source bodies."""
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Vitis workspace 不存在: {workspace}")
    files, has_sources, truncated = _candidate_files(root, max_files)
    compile_dbs = [path for path in files if path.name.casefold() == "compile_commands.json"]
    issues: list[dict] = []
    reference_count = 0
    optimization_flags: set[str] = set()
    app_optimization_flags: set[str] = set()
    seen_refs: set[tuple[str, str]] = set()

    for metadata in files:
        try:
            text = metadata.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        flags = set(re.findall(r"(?<!\w)-(O0|O1|O2|O3|Ofast|Og)\b", text))
        optimization_flags.update(flags)
        if metadata.name.casefold() == "compile_commands.json":
            relative_parts = {part.casefold() for part in metadata.relative_to(root).parts}
            if not relative_parts & {"bsp", "libsrc", "zynq_fsbl", "fsbl"}:
                app_optimization_flags.update(flags)
            # compile_commands contains one absolute source/build path per
            # translation unit. Platform ownership belongs to Vitis metadata;
            # treating every compile entry as a separate reference is noise.
            continue
        for raw_path, context in _extract_paths(text):
            if _looks_like_toolchain(raw_path):
                continue
            target = Path(raw_path).expanduser().resolve(strict=False)
            if _is_inside(target, root):
                continue
            kind = _reference_kind(raw_path, context)
            # Generic absolute output/log paths are noisy and do not describe
            # platform ownership. Keep only migration-relevant references.
            exists = target.exists()
            if kind == "external":
                continue
            key = (kind, os.path.normcase(str(target)))
            if key in seen_refs:
                continue
            seen_refs.add(key)
            reference = {
                "kind": kind,
                "path": _display(target),
                "exists": exists,
                "from": _display(metadata.relative_to(root)),
            }
            reference_count += 1
            severity = "warning" if exists else "error"
            code = "external_workspace_reference" if exists else "missing_workspace_reference"
            issues.append({
                "code": code,
                "severity": severity,
                "message": (
                    f"{kind} 仍引用 workspace 外部路径，复制工程可能不独立"
                    if exists else f"{kind} 引用目标不存在"
                ),
                "file": reference["from"],
                "path": reference["path"],
                "kind": kind,
            })

    vitis_markers = reference_count > 0 or any(
        path.suffix.casefold() in {".xpfm", ".spr"}
        or path.name.casefold() in {"vitis-comp.json", "platform.json", ".project", ".cproject"}
        for path in files
    )
    if has_sources and vitis_markers and not compile_dbs:
        issues.append({
            "code": "compile_database_missing",
            "severity": "warning",
            "message": "未找到 compile_commands.json；编辑器索引可能与真实 BSP/include 不一致",
        })
    effective_flags = app_optimization_flags or optimization_flags
    only_unoptimized = "O0" in effective_flags and not (
        {"O2", "O3", "Ofast"} & effective_flags
    )
    if only_unoptimized:
        issues.append({
            "code": "only_o0_detected",
            "severity": "warning",
            "message": "只检测到 -O0；实时 DSP/CNN 性能验证前请显式使用 -O2/-O3",
        })

    errors = sum(issue["severity"] == "error" for issue in issues)
    status = "error" if errors else "warning" if issues else "pass"
    return {
        "status": status,
        "issue_count": len(issues),
        "issues": issues[:max_issues],
        "issues_truncated": len(issues) > max_issues,
        "files_scanned": len(files),
        "scan_truncated": truncated,
        "compile_commands": {
            "present": bool(compile_dbs),
            "files": [
                _display(path.relative_to(root))
                for path in sorted(compile_dbs, key=lambda item: len(item.parts))[:3]
            ],
        },
        "optimization_flags": sorted(effective_flags),
        "reference_count": reference_count,
    }
