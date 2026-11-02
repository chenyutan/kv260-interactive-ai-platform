"""Bounded, read-only portability preflight for saved Vivado XPR projects.

This audits active saved references, not elaboration, IP regeneration or cache
ownership. XPR macros below were checked against local Vivado 2022.2/2026.1
projects and their on-disk directories. The saved Project/@Path and ImportPath
record provenance; neither overrides the current XPR directory or active File.
No Tcl, HDL or XML entities are executed. Opaque dependencies require review.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath


MAX_XML_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 8 * 1024 * 1024
_MACRO = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_]\w*)")
_TEXT_SOURCE = {".v", ".sv", ".vh", ".svh", ".vhd", ".vhdl", ".xdc"}
_OPAQUE = {".xci", ".xcix", ".bd", ".bxml", ".dcp", ".edf", ".edn", ".ngc", ".tcl"}
_DEPENDENCY = re.compile(
    r'`include\b|\$(?:readmem[hb]|fopen)\b|'
    r'(?<!\w)(?:source|read_xdc|read_verilog|read_vhdl|read_ip|read_checkpoint|eval|uplevel|exec)\s|'
    r'\bfile\s+\w+\s*:\s*\w+\s+open\b', re.I | re.M,
)
_DIRECTORY_OPTIONS = {
    "iprepopath": "ip_repository", "includedirs": "include_directory",
    "verilogincludedirs": "include_directory",
    "ipdefaultoutputpath": "output_directory", "ipuserfilesdir": "output_directory",
    "ipstaticsourcedir": "output_directory", "ipoutputrepo": "output_directory",
}
_FILE_OPTIONS = {"targetconstrsfile", "incrementalcheckpoint", "referencecheckpoint"}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _local_tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _resolve(raw: str, root: Path, macros: dict[str, Path]) -> Path:
    value = raw.strip().replace("\\", "/")
    if not value or len(value) > 4096 or any(c in value for c in "\0\r\n"):
        raise ValueError("empty, oversized or multiline path")
    unknown: set[str] = set()

    def expand(match: re.Match) -> str:
        name = match.group(1) or match.group(2)
        if name not in macros:
            unknown.add(name)
            return match.group(0)
        return macros[name].as_posix()

    value = _MACRO.sub(expand, value)
    if unknown or "$" in value or value.startswith("[") or re.search(r"%[^%]+%", value):
        raise ValueError("unresolved macro or dynamic path")
    # Never reinterpret a foreign drive/UNC path as a relative POSIX filename.
    windows_path = PureWindowsPath(value)
    if windows_path.drive and not windows_path.is_absolute():
        raise ValueError("ambiguous drive-relative path")
    if windows_path.is_absolute() and not Path(value).is_absolute():
        raise ValueError("foreign-platform absolute path")
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def audit_project(xpr: str | Path, *, shared_ip_roots: list[str] | None = None,
                  max_files: int = 240, max_issues: int = 12) -> dict:
    """Check saved paths without launching Vivado or changing project files.

    Only IPRepoPath may use shared_ip_roots. An external RTL/XDC/generated
    wrapper under the same directory remains an escape. A pass means the
    bounded saved-reference check completed; it is never build/cache proof.
    XCI/BD/netlists/checkpoints/scripts and HDL file-loading constructs have
    hidden dependencies and therefore produce review_required, never pass.
    """
    for name, value, upper in (("max_files", max_files, 2000), ("max_issues", max_issues, 100)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
            raise ValueError(f"{name} must be an integer in 1..{upper}")
    if shared_ip_roots is not None and (
        not isinstance(shared_ip_roots, list) or len(shared_ip_roots) > 32
        or any(not isinstance(p, str) or not p.strip() for p in shared_ip_roots)
    ):
        raise ValueError("shared_ip_roots must contain at most 32 nonempty directory paths")
    project = Path(xpr).expanduser().resolve()
    if project.suffix.lower() != ".xpr" or not project.is_file():
        raise ValueError(f"Vivado XPR does not exist: {xpr}")
    root = project.parent
    allowed: list[Path] = []
    for raw in shared_ip_roots or []:
        directory = Path(raw).expanduser()
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError(f"shared_ip_roots entries must be existing absolute directories: {raw}")
        allowed.append(directory.resolve())
    macros = {"PPRDIR": root}
    for name, suffix in (("PSRCDIR", "srcs"), ("PGENDIR", "gen"), ("PRUNDIR", "runs"),
                         ("PCACHEDIR", "cache"), ("PIPUSERFILESDIR", "ip_user_files"),
                         ("PSIMDIR", "sim"), ("PHWDIR", "hw")):
        macros[name] = root / f"{project.stem}.{suffix}"
    issues: list[dict] = []
    reference_count = 0
    internal_count = 0
    shared_count = 0
    external_count = 0
    source_bytes = 0
    truncated = False
    seen: set[tuple[str, str]] = set()
    scanned_sources: set[Path] = set()

    def issue(code: str, severity: str, raw: str = "", **extra) -> None:
        issues.append({"code": code, "severity": severity,
                       **({"reference": raw[:512]} if raw else {}), **extra})

    def result() -> dict:
        incomplete = any(i["severity"] == "review" for i in issues) or truncated
        status = "error" if any(i["severity"] == "error" for i in issues) else (
            "review_required" if incomplete else "pass"
        )
        return {
            "status": status, "scope": "saved_xpr_references",
            "reference_audit_complete": not incomplete,
            "self_contained": status == "pass" and shared_count == 0,
            "reference_count": reference_count, "internal_count": internal_count,
            "allowed_shared_ip_count": shared_count, "external_count": external_count,
            "source_files_scanned": len(scanned_sources), "scan_truncated": truncated,
            "issue_count": len(issues), "issues": issues[:max_issues],
            "issues_truncated": len(issues) > max_issues,
            "vivado_process_starts": 0,
        }

    try:
        with project.open("rb") as stream:
            data = stream.read(MAX_XML_BYTES + 1)
    except OSError:
        issue("xpr_unreadable", "review")
        return result()
    if len(data) > MAX_XML_BYTES:
        issue("xpr_oversized", "review")
        return result()
    # Removing NULs also catches DTD/entity declarations in UTF-16/32 input.
    safe_probe = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in safe_probe or b"<!ENTITY" in safe_probe:
        issue("xml_entities_forbidden", "review")
        return result()
    try:
        tree = ET.fromstring(data)
    except (ET.ParseError, ValueError):
        issue("xpr_invalid_xml", "review")
        return result()
    if _local_tag(tree) != "Project" or tree.attrib.get("Version") != "7":
        issue("unsupported_xpr_schema", "review")
        return result()

    def check(raw: str, kind: str, origin: str, *, required: bool = True) -> None:
        nonlocal reference_count, internal_count, shared_count, external_count
        nonlocal truncated, source_bytes
        key = (kind, raw)
        if key in seen:
            return
        seen.add(key)
        if reference_count >= max_files:
            if not truncated:
                issue("reference_scan_truncated", "review")
            truncated = True
            return
        reference_count += 1
        try:
            target = _resolve(raw, root, macros)
        except (OSError, RuntimeError, ValueError):
            issue("unresolved_path", "review", raw, kind=kind, origin=origin[:128])
            return
        local = _inside(target, root)
        shared = kind == "ip_repository" and any(_inside(target, p) for p in allowed)
        if local:
            internal_count += 1
        elif shared:
            shared_count += 1
        else:
            external_count += 1
            issue("external_project_reference", "error", raw, kind=kind, origin=origin[:128])
        try:
            exists = target.is_dir() if kind.endswith("directory") or kind == "ip_repository" else target.is_file()
        except OSError:
            exists = False
        if required and not exists:
            issue("missing_project_reference", "error", raw, kind=kind, origin=origin[:128])
        if not local or not exists or kind != "source":
            return
        suffix = target.suffix.lower()
        if suffix in _OPAQUE or suffix not in _TEXT_SOURCE | {".mem", ".hex", ".bin", ".coe", ".dat", ".txt"}:
            issue("opaque_dependency", "review", raw, kind=kind, origin=origin[:128])
            return
        if suffix not in _TEXT_SOURCE or target in scanned_sources:
            return
        scanned_sources.add(target)
        try:
            with target.open("rb") as stream:
                body = stream.read(min(MAX_SOURCE_BYTES, MAX_TOTAL_SOURCE_BYTES - source_bytes) + 1)
        except OSError:
            issue("source_unreadable", "review", raw)
            return
        source_bytes += len(body)
        if len(body) > MAX_SOURCE_BYTES or source_bytes > MAX_TOTAL_SOURCE_BYTES:
            issue("source_scan_oversized", "review", raw)
            return
        text = body.decode("utf-8", errors="replace")
        if "\0" in text or "\ufffd" in text:
            issue("source_encoding_review", "review", raw)
            return
        # This is a conservative detector, not an HDL/Tcl dependency evaluator.
        # Do not claim includes, readmem or sourced scripts were resolved.
        if _DEPENDENCY.search(text):
            issue("source_dependency_review", "review", raw)

    for element in tree.iter():
        tag = _local_tag(element)
        name = element.attrib.get("Name", "")
        folded = name.casefold()
        if tag == "File":
            if "Path" not in element.attrib:
                issue("unrecognized_file_reference", "review")
            else:
                check(element.attrib["Path"], "source", "File/@Path")
        elif tag in {"Option", "Attr"} and element.attrib.get("Val"):
            value = element.attrib["Val"]
            if folded in _DIRECTORY_OPTIONS:
                # Observed XPR stores repeated Option entries, one path per Val.
                # Do not guess the meaning of a Tcl list or shell-style quoting.
                if value.startswith(("{", '"')) or ";" in value:
                    issue("path_list_requires_review", "review", value, origin=name[:128])
                else:
                    kind = _DIRECTORY_OPTIONS[folded]
                    check(value, kind, name, required=kind != "output_directory")
            elif folded in _FILE_OPTIONS or (".tcl." in folded):
                check(value, "source", name)
            elif folded.startswith("compiledlibdir"):
                check(value, "output_directory", name, required=False)
            elif folded == "importpath":
                # Historical source of an imported copy, not an active dependency.
                continue
            elif re.search(r"\$[A-Z_]*DIR|[/\\]|\.[a-z]{1,4}$", value, re.I) and (
                folded.endswith(("path", "file", "dir", "dirs"))
                or folded.startswith("cosim") or folded.endswith(".more_options")
            ):
                issue("unrecognized_path_option", "review", value, origin=name[:128])
        elif tag in {"Run", "FileSet", "DefaultLaunch", "GeneratedRun"}:
            for attribute in ("Dir", "RelSrcDir", "RelGenDir", "AutoIncrementalDir", "AutoRQSDir"):
                if element.attrib.get(attribute):
                    check(element.attrib[attribute], "output_directory", f"{tag}/@{attribute}", required=False)
            for attribute in ("IncrementalCheckpoint", "ReferenceCheckpoint"):
                if element.attrib.get(attribute):
                    check(element.attrib[attribute], "source", f"{tag}/@{attribute}")
        elif tag not in {"Project", "FileInfo"} and any(
            attribute in element.attrib for attribute in ("Path", "File", "Dir")
        ):
            issue("unrecognized_reference_element", "review", origin=tag[:128])
    return result()
