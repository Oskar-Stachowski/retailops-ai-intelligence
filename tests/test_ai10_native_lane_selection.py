"""An unrelated or shared change cannot silently omit a native acceptance lane."""

import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / "scripts/select_ai10_native_lanes.py"
spec = importlib.util.spec_from_file_location("ai10_native_lane_selector", path)
selector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selector)


@pytest.mark.parametrize(
    "paths,expected",
    [
        ({"scripts/check_anomaly_oci.py"}, (False, True)),
        ({"scripts/accept_anomaly_lifecycle.py"}, (False, True)),
        ({"scripts/check_stockout_final_acceptance.py"}, (True, False)),
        (
            {"scripts/check_anomaly_oci.py", "scripts/check_stockout_final_acceptance.py"},
            (True, True),
        ),
        ({"scripts/run_ai10_model_consumer.py"}, (True, True)),
        ({"docs/reference/ai10-native-output-consumer.json"}, (True, True)),
        ({"src/retailops_ai/intelligence_events/acceptance_export.py"}, (True, True)),
        ({"new_unclassified_implementation.py"}, (True, True)),
        (set(), (True, True)),
    ],
)
def test_shared_unknown_or_unavailable_range_requires_both_native_models(paths, expected):
    assert selector.lanes(paths) == expected


@pytest.mark.parametrize(
    "manual,expected",
    [("all", (True, True)), ("stockout", (True, False)), ("anomaly", (False, True))],
)
def test_manual_scope_is_explicit(manual, expected):
    assert selector.lanes(set(), manual) == expected


def test_invalid_manual_scope_cannot_become_successful_empty_acceptance():
    with pytest.raises(ValueError, match="lane_invalid"):
        selector.lanes(set(), "none")
