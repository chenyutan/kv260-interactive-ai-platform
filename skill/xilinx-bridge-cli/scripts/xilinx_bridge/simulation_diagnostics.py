"""Bounded observations from explicitly supplied xvlog/xvhdl/xelab logs.

No log means unknown, not a cache hit. ``--incr`` means requested, not reused.
The retained 2022.2 XSIM 43-4323 message establishes xelab object-link reuse,
not reuse of the complete elaboration or simulation result. Error classes
describe vendor messages; suggested checks are not established root causes.
This module never starts tools, recursively discovers logs or evaluates HDL.
"""
from __future__ import annotations

import math
import re
import stat
from pathlib import Path


_ENGINES = ("xvlog", "xvhdl", "xelab")
_ERROR = re.compile(r"^\s*(?:ERROR|FATAL)(?:\s*:|\s+)", re.I)
_CODE = re.compile(r"\[([A-Za-z][\w-]*\s+\d+-\d+)\]")
_ENGINE = re.compile(r"\b(xvlog|xvhdl|xelab)(?:\.exe)?\b", re.I)
_LOCATION = re.compile(r"\[([^\[\]\r\n]+\.(?:svh?|vh?|vhdl?|xdc)):(\d+)(?::(\d+))?\]\s*$", re.I)
_RULES = (
    ("include_path", r"include\s+file[^\r\n]{0,240}(?:not found|cannot|could not|failed)|"
     r"(?:cannot|could not|failed to)[^\r\n]{0,80}(?:find|open)[^\r\n]{0,160}include\s+file",
     "Check include directories and the named header path; the message alone does not establish why it is unavailable."),
    ("library_dependency", r"(?:library|package)[^\r\n]{0,240}(?:not found|not mapped|not compiled|does not exist)|"
     r"(?:cannot|could not|failed to)[^\r\n]{0,80}(?:find|open|load|map)[^\r\n]{0,160}\blibrary\b|"
     r"not compiled[^\r\n]{0,120}\blibrary\b",
     "Check the selected simulation library mappings, package compile order and matching tool-version libraries."),
    ("missing_module", r"\b(?:module|design unit|entity)\b[^\r\n]{0,240}(?:not found|does not exist)|"
     r"(?:cannot|could not|unable to)\s+find\s+(?:module|design unit|entity)\b",
     "Check the named design unit, fileset membership, generated simulation models and compile order."),
    ("syntax_error", r"\bsyntax\s+error\b|\bparse\s+error\b|\bparsing\s+error\b",
     "Inspect the reported source location and preceding tokens, using the configured HDL language mode."),
    ("interface_mismatch", r"\bport\b[^\r\n]{0,200}(?:not found|does not exist|not declared)|"
     r"(?:has no|cannot find)\s+port\b|\bformal\s+port\b[^\r\n]{0,100}(?:mismatch|missing)",
     "Compare the instance ports against the compiled module/entity and any generated wrapper; stale wrappers are only one possibility."),
    ("tool_environment", r"access\s+(?:is\s+)?denied|permission denied|"
     r"error while loading shared libraries|(?:\.dll|\.so(?:\.\d+)*)[^\r\n]{0,120}(?:not found|cannot open)",
     "Check the indicated host permission or runtime-library failure before changing HDL."),
    ("source_path", r"(?:cannot|could not|unable to|failed to)\s+(?:open|read|find)\s+(?:source\s+)?file\b|"
     r"(?:source\s+)?file[^\r\n]{0,240}(?:does not exist|not found)|no such file or directory",
     "Check the named source path, working directory and copied-project references; do not assume a spelling or permission cause."),
    ("duplicate_definition", r"\b(?:module|entity|package|design unit)\b[^\r\n]{0,240}(?:already (?:declared|defined)|duplicate)|"
     r"\bduplicate\s+(?:definition|module|entity|design unit)\b",
     "Check whether the same design unit is supplied more than once or by different source versions."),
    ("unresolved_symbol", r"\b(?:not declared|undeclared|undefined identifier|not defined)\b",
     "Inspect the identifier, scope, package visibility and earlier compile errors; the message does not identify which dependency is wrong."),
    ("stage_failure", r"\b(?:compile|compilation|elaborate|elaboration)\b[^\r\n]{0,100}(?:failed|failure)|"
     r"(?:failed|failure)[^\r\n]{0,100}\b(?:compile|compilation|elaborate|elaboration)\b",
     "Inspect the compiler/elaborator error before this stage-failed summary; this message is not a root cause."),
)
_COMPILED_RULES = [(name, re.compile(pattern, re.I), check) for name, pattern, check in _RULES]
_MAX_LINE_CHARS = 4096
_MAX_MESSAGE_CHARS = 360


def classify_error_line(line: str) -> dict | None:
    """Classify one explicit ERROR/FATAL line, without inferring root cause."""
    sample = line[:_MAX_LINE_CHARS]
    if not _ERROR.match(sample):
        return None
    code = _CODE.search(sample)
    item = {
        "category": "unclassified_error", "code": code.group(1) if code else "",
        "message": sample.strip()[:_MAX_MESSAGE_CHARS],
        "message_truncated": len(sample.strip()) > _MAX_MESSAGE_CHARS or len(line) > _MAX_LINE_CHARS,
        "suggested_check": "Inspect the original compiler/elaborator message; no supported signature matched.",
    }
    for name, pattern, check in _COMPILED_RULES:
        if pattern.search(sample):
            item["category"] = name
            item["suggested_check"] = check
            break
    location = _LOCATION.search(sample)
    if location:
        item["source_location"] = {"path": location.group(1)[-512:], "line": int(location.group(2))}
        if location.group(3):
            item["source_location"]["column"] = int(location.group(3))
    return item


def _stage_from_name(name: str) -> str:
    return {"xvlog.log": "xvlog", "xvhdl.log": "xvhdl", "xelab.log": "xelab",
            "elaborate.log": "xelab"}.get(name.casefold(), "unknown")


def _read_regions(stream, size: int, budget: int) -> tuple[list[tuple[str, bytes]], int]:
    """Read at most budget bytes; never splice lines across an omitted middle."""
    if size <= budget:
        data = stream.read(size)
        return [("full", data)], len(data)
    head_size = budget // 2
    head = stream.read(head_size)
    stream.seek(size - (budget - head_size))
    tail = stream.read(budget - head_size)
    # Drop partial boundary lines so sampling cannot manufacture an ERROR or
    # cache marker from disconnected fragments. A giant single line is unknown.
    head_end = head.rfind(b"\n")
    tail_start = tail.find(b"\n")
    regions = [("head", head[:head_end + 1] if head_end >= 0 else b""),
               ("tail", tail[tail_start + 1:] if tail_start >= 0 else b"")]
    return regions, len(head) + len(tail)


def diagnose_logs(paths: list[str | Path], *, not_before: float | None = None,
                  not_after: float | None = None,
                  max_files: int = 6, max_bytes_per_file: int = 65536,
                  max_total_bytes: int = 262144, max_issues: int = 6) -> dict:
    """Return deterministic bounded observations for explicitly chosen log paths.

    Caller chooses the current simulation directory; no recursive scan occurs.
    ``not_before``/``not_after`` are inclusive mtime bounds (Unix seconds).
    Logs outside the job window or changed while reading are not evidence.
    Head/tail sampling records incomplete coverage. Empty, missing, stale and
    unreadable logs cannot establish successful compilation or cache reuse.
    ``no_errors_in_sample`` is an observation, never a compile/PASS verdict.
    """
    if not isinstance(paths, (list, tuple)) or any(not isinstance(p, (str, Path)) for p in paths):
        raise ValueError("paths must be a list of explicit log paths")
    for name, value, low, high in (
        ("max_files", max_files, 1, 16), ("max_bytes_per_file", max_bytes_per_file, 1024, 262144),
        ("max_total_bytes", max_total_bytes, 1024, 1048576), ("max_issues", max_issues, 1, 12),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{name} must be an integer in {low}..{high}")
    for name, timestamp in (("not_before", not_before), ("not_after", not_after)):
        if timestamp is not None and (
            isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
        ):
            raise ValueError(f"{name} must be a finite Unix timestamp")
    if not_before is not None and not_after is not None and not_before > not_after:
        raise ValueError("not_before must not exceed not_after")
    stages = {name: {"activity": [], "incremental_requested": None,
                     "cache_state": "unknown", "cache_reason": "unknown"} for name in _ENGINES}
    sources: list[dict] = []
    issues: list[dict] = []
    unique_issues: set[tuple] = set()
    seen_paths: set[Path] = set()
    bytes_read = 0
    issue_count = 0
    observations = False
    files_truncated = False
    sampled = False

    for raw_path in paths:
        try:
            path = Path(raw_path).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            path = Path(str(raw_path))
        if path in seen_paths:
            continue
        seen_paths.add(path)
        if len(sources) >= max_files:
            files_truncated = True
            break
        record = {"path": str(path)[-512:], "status": "unknown", "bytes_read": 0, "sampled": False}
        sources.append(record)
        try:
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                record["status"] = "not_regular"
                continue
            if not_before is not None and metadata.st_mtime < not_before:
                record["status"] = "stale"
                continue
            if not_after is not None and metadata.st_mtime > not_after:
                record["status"] = "future"
                continue
            budget = min(max_bytes_per_file, max_total_bytes - bytes_read)
            if budget <= 0:
                record["status"] = "budget_exhausted"
                continue
            with path.open("rb") as stream:
                regions, amount = _read_regions(stream, metadata.st_size, budget)
        except FileNotFoundError:
            record["status"] = "missing"
            continue
        except (OSError, ValueError):
            record["status"] = "unreadable"
            continue
        bytes_read += amount
        record.update(status="read" if amount else "empty", bytes_read=amount,
                      sampled=metadata.st_size > budget)
        # A following run may replace or rewrite the path while this descriptor
        # is open. Count the consumed bytes but discard every observation from
        # this log unless identity, exact timestamp and size remain unchanged.
        try:
            after = path.stat()
            unchanged = all(getattr(metadata, attribute) == getattr(after, attribute)
                            for attribute in ("st_dev", "st_ino", "st_mtime_ns", "st_size"))
        except OSError:
            unchanged = False
        if not unchanged:
            record["status"] = "changed_during_read"
            sampled = sampled or record["sampled"]
            continue
        if amount != min(metadata.st_size, budget):
            # A short read is also insufficient evidence even if metadata
            # happened to remain unchanged.
            record["status"] = "short_read"
            record["sampled"] = sampled = True
            continue
        sampled = sampled or record["sampled"]
        stage = _stage_from_name(path.name)
        for region, data in regions:
            for line_number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                line = line[:_MAX_LINE_CHARS + 1]
                if len(line) > _MAX_LINE_CHARS or "\0" in line or "\ufffd" in line:
                    record["sampled"] = sampled = True
                    continue
                # Do not infer a tool invocation merely from a filename or -incr
                # mentioned in an error. Real xelab headers start with Running:.
                if re.match(r"\s*(?:Running|Command):", line, re.I):
                    command = _ENGINE.search(line)
                    if command:
                        stage = command.group(1).lower()
                        if "tool_invocation" not in stages[stage]["activity"]:
                            stages[stage]["activity"].append("tool_invocation")
                        stages[stage]["incremental_requested"] = bool(re.search(r"(?:^|\s)--?incr(?:\s|$)", line))
                        observations = True
                error = classify_error_line(line)
                if error:
                    observations = True
                    key = (error["category"], error["code"], line.strip())
                    if key not in unique_issues:
                        unique_issues.add(key)
                        issue_count += 1
                        if len(issues) < max_issues:
                            issues.append({**error, "stage": stage, "evidence": {
                                "log_index": len(sources) - 1, "region": region, "region_line": line_number,
                            }})
                    continue
                if stage not in stages:
                    continue
                activity = stages[stage]["activity"]
                for label, pattern in (
                    ("analyzing_hdl", r"\bAnalyzing\s+(?:SystemVerilog|Verilog|VHDL)\s+file\b"),
                    ("static_elaboration", r"^(?:Starting|Completed) static elaboration\b"),
                    ("compiling_modules", r"^Compiling module\s+"),
                    ("snapshot_built", r"^Built simulation snapshot\s+"),
                ):
                    if re.search(pattern, line, re.I):
                        observations = True
                        if label not in activity:
                            activity.append(label)
                if stage == "xelab" and re.match(
                    r"\s*INFO:\s+\[XSIM 43-4323\]\s+No Change in HDL\.\s+"
                    r"Linking previously generated obj files to create kernel\s*$", line, re.I
                ):
                    observations = True
                    stages[stage]["cache_state"] = "object_link_reuse_observed"
                    stages[stage]["cache_reason"] = "vendor_reported_no_hdl_change"
                    stages[stage].setdefault("cache_evidence", {
                        "log_index": len(sources) - 1, "region": region,
                        "region_line": line_number, "message": line.strip()[:_MAX_MESSAGE_CHARS],
                    })
                    if "object_link_reused" not in activity:
                        activity.append("object_link_reused")
        # Fixed ordering makes projections independent of duplicated log order.
        for item in stages.values():
            item["activity"].sort()
    for item in stages.values():
        if item["cache_state"] == "object_link_reuse_observed":
            item["reuse_scope"] = "xelab_objects_only"
    return {
        "status": "errors_observed" if issue_count else "no_errors_in_sample" if observations else "unknown",
        "scope": "simulation_compile_logs", "stages": stages,
        "issue_count": issue_count, "issues": issues, "issues_truncated": issue_count > len(issues),
        "coverage": {"bytes_read": bytes_read, "files_truncated": files_truncated,
                     "sampled": sampled,
                     "complete": not files_truncated and not sampled
                     and all(record["status"] == "read" for record in sources) and bool(sources),
                     "logs": sources},
        "execution_verified": False,
    }
