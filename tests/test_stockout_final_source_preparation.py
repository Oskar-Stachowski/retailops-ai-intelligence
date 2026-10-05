"""Prospective source pins, old resource proof and real preparation without fits."""

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from test_stockout_features import native as native

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stockout_future", ROOT / "scripts/prepare_stockout_final_sources.py"
)
assert SPEC and SPEC.loader
future = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(future)


@pytest.mark.parametrize("seed", [42, 137, 2026])
def test_fixed_future_world_has_no_fit_or_final_score_permission(seed):
    profile = json.loads(future.profile_path(ROOT, seed).read_bytes())
    future.validate_profile(profile, seed)
    baseline = json.loads((ROOT / "docs/reference/stockout-resource-baseline-30.json").read_bytes())
    original = deepcopy(baseline)
    result = future.preflight(profile, baseline, 8 * 1024**3)
    assert result["ready_to_attempt"]
    assert not result["new_source_qualified"] and not result["independent_quality_accepted"]
    assert baseline == original
    assert result["measured_baseline_producer_commit"] != result["proposed_producer_commit"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("producer_commit", "0" * 40),
        ("consumer_baseline_commit", "1" * 40),
        ("physical_origin_upper_bound", 6121),
        ("model_training_permitted", True),
        ("final_test_outcomes_evaluated", True),
        ("local_disk_policy", {"minimum_free_bytes": 1}),
        ("generation", {}),
        ("split_policy", {}),
        ("budgets", {}),
        ("unchanged_consumer_limits", {}),
    ],
)
def test_resealed_profile_cannot_change_source_geometry_permissions_or_caps(field, value):
    profile = json.loads(future.profile_path(ROOT, 42).read_bytes())
    profile[field] = value
    with pytest.raises(ValueError, match="frozen_profile"):
        future.validate_profile(profile, 42)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "failed"),
        ("producer_commit", future.PRODUCER),
        ("final_test_outcomes_evaluated", True),
        ("whole_pilot_budget_passed", False),
    ],
)
def test_prior_receipt_cannot_be_reclassified_as_a_new_world_quality_proof(field, value):
    baseline = json.loads((ROOT / "docs/reference/stockout-resource-baseline-30.json").read_bytes())
    baseline[field] = value
    profile = json.loads(future.profile_path(ROOT, 42).read_bytes())
    with pytest.raises(ValueError, match="unqualified_resource_baseline"):
        future.preflight(profile, baseline, 8 * 1024**3)


def test_disk_shortfall_prevents_attempt_without_changing_local_reserve():
    profile = json.loads(future.profile_path(ROOT, 42).read_bytes())
    baseline = json.loads((ROOT / "docs/reference/stockout-resource-baseline-30.json").read_bytes())
    result = future.preflight(profile, baseline, future.RESERVE)
    assert not result["ready_to_attempt"] and not result["checks"]["free_disk"]
    assert profile["local_disk_policy"]["minimum_free_bytes"] == 50 * 1024**3


@pytest.mark.parametrize("change", ["platform", "action", "os", "repository"])
def test_remote_execution_cannot_run_on_local_or_foreign_surface(monkeypatch, change):
    monkeypatch.setattr(future.sys, "platform", "linux")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Oskar-Stachowski/retailops-ai-intelligence")
    if change == "platform":
        monkeypatch.setattr(future.sys, "platform", "darwin")
    else:
        monkeypatch.setenv(
            {"action": "GITHUB_ACTIONS", "os": "RUNNER_OS", "repository": "GITHUB_REPOSITORY"}[
                change
            ],
            "invalid",
        )
    with pytest.raises(ValueError, match="isolated_github_runner"):
        future.runner_boundary()


def test_real_fixture_import_curated_feature_upstream_label_temporal_stops_before_models(
    native, tmp_path, monkeypatch
):
    root, _, _, _ = native
    probe = future.load_helper(ROOT, "measure_stockout_pipeline.py")
    from retailops_ai.stockout_temporal_series import bundle
    from retailops_ai.stockout_training import development

    def forbidden(*args, **kwargs):
        pytest.fail("source preparation must never assemble outcomes or fit models")

    monkeypatch.setattr(development, "build_development", forbidden)
    monkeypatch.setattr(bundle, "assemble_partitioned_development", forbidden)
    # Pytest adds the repository root (which contains data/ as a namespace).
    # Match the real -P consumer process by removing that path, retaining src
    # and installed dependencies; the import-isolation guard stays operational.
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p).resolve() != ROOT])
    importlib.util.find_spec("retailops_ai")
    (tmp_path / "producer.json").write_text(
        json.dumps(
            dict(
                paths={
                    "facts_export": str(root / "fixture/facts"),
                    "private_export": str(root / "fixture/private"),
                    "source": str(root / "fixture/facts"),
                    "qualification": str(root / "fixture/private"),
                }
            )
        )
    )
    (tmp_path / "pilot-config.json").write_text(json.dumps(dict(split_policy=future.split())))
    future.consume(tmp_path, probe)
    receipt = json.loads((tmp_path / "consumer.json").read_bytes())
    assert [s["stage"] for s in receipt["stages"]] == [
        "facts_import",
        "curated",
        "private_import",
        "features",
        "labels",
        "upstream",
        "temporal",
    ]
    assert all(count > 0 for count in receipt["physical_origins"].values())
    assert not receipt["model_training_performed"] and not receipt["final_test_outcomes_evaluated"]
    assert not receipt["independent_quality_accepted"] and not receipt["model_promoted"]
    assert all(
        (tmp_path / n / "manifest.json").is_file()
        for n in ("features", "labels", "upstream", "temporal")
    )
    assert not (tmp_path / "development.json").exists()
