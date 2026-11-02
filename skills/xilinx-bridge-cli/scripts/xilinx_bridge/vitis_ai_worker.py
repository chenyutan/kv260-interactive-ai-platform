"""Background Job entry point for long Vitis AI workflows."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import vitis_ai


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 2:
        print("XAB_VAI_JOB_ERROR=worker expects request.json and result.json")
        return 2
    request_path = Path(values[0]).resolve()
    result_path = Path(values[1]).resolve()
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = vitis_ai.flow(**request)
        returncode = 0
    except Exception as exc:  # noqa: BLE001 - worker must always persist a terminal result
        result = {"status": "error", "message": str(exc)}
        returncode = 2
    result_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = result_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, result_path)
    print("XAB_VAI_JOB_RESULT=" + json.dumps(
        {"status": result.get("status"), "action": result.get("action")},
        ensure_ascii=False, separators=(",", ":"),
    ))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
