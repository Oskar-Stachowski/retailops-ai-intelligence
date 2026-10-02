"""Preserve original JUnit failures and bind their explicit, test-specific reruns."""

from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from retailops_ai.source_snapshot.files import SnapshotError, file_hash


def test_receipts(paths: list[Path]) -> dict[str, Any]:
    records: dict[tuple[str, str], tuple[str, str]] = {}
    receipts = {}
    resolutions = []
    for path in paths:
        path = path.resolve()
        size, digest = file_hash(path.parent, path.name)
        if size > 32 * 1024**2:
            raise SnapshotError("functional_test_receipt_size")
        document = ElementTree.parse(path)  # noqa: S314 -- local bounded pytest receipt
        cases = list(document.getroot().iter("testcase"))
        if not cases:
            raise SnapshotError("functional_test_receipt_empty")
        receipts[str(path)] = {"sha256": digest, "tests": len(cases)}
        for case in cases:
            name = (case.get("classname", ""), case.get("name", ""))
            status = (
                "failed"
                if case.find("failure") is not None or case.find("error") is not None
                else "skipped"
                if case.find("skipped") is not None
                else "passed"
            )
            previous = records.get(name)
            if previous is not None and previous[0] == "failed" and status == "passed":
                resolutions.append(
                    {"test": list(name), "original_failure": previous[1], "passed_rerun": str(path)}
                )
            # A skipped rerun cannot erase an observed failure.
            if previous is None or previous[0] != "failed" or status != "skipped":
                records[name] = status, str(path)
    if not records or any(state != "passed" for state, _ in records.values()):
        raise SnapshotError("functional_tests_not_all_passed_or_explicitly_resolved")
    return {
        "status": "passed",
        "effective_tests": len(records),
        "files": receipts,
        "resolved_failures": resolutions,
    }
