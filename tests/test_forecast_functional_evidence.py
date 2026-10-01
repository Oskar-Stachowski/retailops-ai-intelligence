"""A passing unrelated test or skipped rerun must never conceal an original failure."""

import pytest

from retailops_ai.forecasting.functional_evidence import test_receipts as receipts
from retailops_ai.source_snapshot.files import SnapshotError


def test_rerun_resolves_only_the_same_failed_test_and_preserves_receipts(tmp_path):
    first, unrelated, second = (
        tmp_path / name for name in ("first.xml", "unrelated.xml", "second.xml")
    )
    first.write_text(
        '<testsuite><testcase classname="server" name="loopback"><failure>denied</failure></testcase><testcase classname="model" name="median"/></testsuite>'
    )
    unrelated.write_text('<testsuite><testcase classname="other" name="loopback"/></testsuite>')
    second.write_text('<testsuite><testcase classname="server" name="loopback"/></testsuite>')
    with pytest.raises(SnapshotError, match="not_all_passed"):
        receipts([first, unrelated])
    report = receipts([first, unrelated, second])
    assert report["effective_tests"] == 3
    assert report["resolved_failures"] == [
        {
            "test": ["server", "loopback"],
            "original_failure": str(first),
            "passed_rerun": str(second),
        }
    ]
    assert "failure" in first.read_text()
    second.write_text(
        '<testsuite><testcase classname="server" name="loopback"><skipped/></testcase></testsuite>'
    )
    with pytest.raises(SnapshotError, match="not_all_passed"):
        receipts([first, second])
