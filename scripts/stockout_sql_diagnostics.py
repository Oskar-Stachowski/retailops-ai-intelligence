"""Publish only test identifiers, source frames, exception types and known source error codes."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "reports/ai05-v12-lifecycle-acceptance.log"
OUTPUT = ROOT / "reports/ai08-stockout-sql-diagnostics.json"


def main() -> int:
    text = (
        LOG.read_bytes()[-2 * 1024**2 :].decode("utf-8", "replace")
        if LOG.is_file() and not LOG.is_symlink()
        else ""
    )
    known = set()
    for folder in ("stockout_jobs", "stockout_lifecycle", "stockout_runtime"):
        for path in (ROOT / "src/retailops_ai" / folder).glob("*.py"):
            known.update(
                re.findall(r"[\"\'](stockout[_-][a-z0-9_-]{1,100})[\"\']", path.read_text())
            )
    errors = set()
    for line in text.splitlines():
        if line.startswith("E "):
            errors.update(code for code in known if code in line)
    report = dict(
        schema_version="stockout-sql-safe-diagnostics-1.0.0",
        log_present=bool(text),
        failed_tests=sorted(
            set(re.findall(r"^FAILED (tests/[a-zA-Z0-9_/]+\.py::[a-zA-Z0-9_]+)", text, re.M))
        ),
        source_frames=sorted(
            set(re.findall(r"\b((?:tests|src/retailops_ai)/[a-zA-Z0-9_/]+\.py:\d+)\b", text))
        ),
        exception_types=sorted(
            set(re.findall(r"^E\s+([a-zA-Z0-9_.]*(?:Error|Exception|Lost)):", text, re.M))
        ),
        known_source_error_codes=sorted(errors),
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
