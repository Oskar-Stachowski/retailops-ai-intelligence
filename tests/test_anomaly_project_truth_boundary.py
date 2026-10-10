"""Legacy SKU-only labels cannot silently qualify complete Project scenarios."""

from datetime import date
from pathlib import Path

import pytest

from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.anomaly_evaluation import source_truth as adapter


@pytest.mark.parametrize("profile", ["ai-dev", "ai-training"])
def test_project_truth_rejects_legacy_clean_rule_before_any_private_truth_read(
    monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("unsupported Project truth was opened")

    monkeypatch.setattr(adapter, "read_bytes", forbidden)
    with pytest.raises(ValueError, match="requires_independent_spillover_verification"):
        adapter.source_truth(
            Path("/unopened-project/scenario.json"),
            {"descriptor": {"resolved_parameters": {"profile": profile}}},
            (),
            Window(start=date(2026, 5, 25), end=date(2026, 5, 31)),
        )
