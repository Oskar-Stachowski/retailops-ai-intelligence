"""Freeze original rules/legacy costs, without Project generation or model fitting."""

import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import development_policy_freeze as freeze
from retailops_ai.evaluation_campaign.development_policy_freeze import (
    DevelopmentPolicySet,
    FrozenDevelopmentPolicies,
)
from retailops_ai.source_snapshot.files import SnapshotError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def policies():
    return DevelopmentPolicySet.model_validate_json(
        (ROOT / "contracts/evaluation/v38/development_policies.default.json").read_bytes()
    )


@pytest.fixture
def declared_catalog(tmp_path, monkeypatch, policies):
    """Actual bounded native subprocess and hash checks; Git identity is a declared double."""
    producer = tmp_path / "declared-native-producer"
    producer.mkdir()
    for name in policies.catalog.code_sha256:
        path = producer / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Explicit native metadata double; no generation entry point.\n")
    categories = [{"id": c} for c in policies.catalog.category_inventory]
    (producer / "data/generator/dimensions.py").write_text(
        "def _categories():\n    return " + repr(categories) + "\n"
    )
    fields = policies.model_dump(mode="json")
    fields["catalog"]["code_sha256"] = {
        name: hashlib.sha256((producer / name).read_bytes()).hexdigest()
        for name in policies.catalog.code_sha256
    }
    declared = DevelopmentPolicySet.model_validate_json(canonical_bytes(fields))
    calls = []

    def verify_pin(path, commit):
        assert path == producer and commit == declared.catalog.producer_commit
        calls.append((path, commit))

    monkeypatch.setattr(freeze, "_producer_pin", verify_pin)
    return producer, declared, calls


def test_default_uses_original_reference_rules_and_complete_dimensions(policies):
    for filename in ("primary.zip", "reference.zip"):
        with zipfile.ZipFile(ROOT / "docs/evidence/07-ready" / filename) as archive:
            original = json.loads(archive.read("model_card.json"))["metrics"]["policy"]
        assert policies.anomaly_reference_quality.model_dump(mode="json") == original
    decision = json.loads(
        (ROOT / "docs/reference/stockout-final-selected-policy-v2.json").read_bytes()
    )["spec"]
    assert policies.stockout_decision_reference.model_dump(mode="json") == decision
    assert policies.stockout_labels.horizon_days == 7
    assert policies.stockout_quality["development"].expected_stock_location_count == 3
    assert policies.stockout_quality["final"].expected_stock_location_count == 4
    assert all(
        p.maximum_expected_calibration_error == 0.15 for p in policies.stockout_quality.values()
    )
    assert policies.uncertainty.minimum_eligible_time_blocks == 8
    assert policies.uncertainty.minimum_eligible_series_clusters == 30
    assert not policies.actual_source_catalog_verified and not policies.model_fit_authorized
    assert not policies.final_test_access_authorized and not policies.stage_ready


def test_freeze_roundtrip_retains_actual_documents_costs_and_private_write_once(
    tmp_path, declared_catalog
):
    producer, policies, calls = declared_catalog
    output = tmp_path / "policies.json"
    result = freeze.freeze_development_policies(
        output, policies=policies, evidence=ROOT / "docs/evidence", producer=producer
    )
    assert result == freeze.load_frozen_development_policies(output)
    assert len(calls) == 2
    assert result.policies == policies
    assert result.context.selection_policy_sha256 == policies.required_groups.content_sha256()
    assert result.context.legacy.published_fit_starts == 44
    assert result.context.legacy.legacy_budget_available == 0
    assert result.context.legacy.full_historical_cost == "unknown_not_zero"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    result.require_producer(policies.catalog.producer_commit)
    with pytest.raises(SnapshotError, match="producer_changed"):
        result.require_producer("0" * 40)
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        freeze.freeze_development_policies(
            output, policies=policies, evidence=ROOT / "docs/evidence", producer=producer
        )
    assert output.read_bytes() == original


@pytest.mark.parametrize(
    "attack",
    [
        "missing_seed",
        "missing_scenario",
        "category",
        "stock_locations",
        "missing_final",
        "catalog_code",
        "grant",
    ],
)
def test_incomplete_or_authority_granting_policy_rejected(policies, attack):
    fields = policies.model_dump(mode="json")
    if attack == "missing_seed":
        fields["seed_weights"].pop("137")
    elif attack == "missing_scenario":
        fields["scenario_weights"].pop(next(iter(fields["scenario_weights"])))
    elif attack == "category":
        fields["catalog"]["category_inventory"].pop()
    elif attack == "stock_locations":
        fields["stockout_quality"]["development"]["expected_stock_location_count"] = 2
    elif attack == "missing_final":
        fields["stockout_quality"].pop("final")
    elif attack == "catalog_code":
        fields["catalog"]["code_sha256"].pop(next(iter(fields["catalog"]["code_sha256"])))
    else:
        fields["model_fit_authorized"] = True
    with pytest.raises(ValidationError):
        DevelopmentPolicySet.model_validate_json(canonical_bytes(fields))


@pytest.mark.parametrize("attack", ["code", "catalog", "commit"])
def test_catalog_mismatch_rejected_before_frozen_file_exists(
    tmp_path, declared_catalog, monkeypatch, attack
):
    producer, policies, _ = declared_catalog
    if attack == "code":
        (producer / "data/generator/dimensions.py").write_text(
            "raise AssertionError('must not execute altered code')\n"
        )
    elif attack == "catalog":
        (producer / "data/generator/dimensions.py").write_text(
            "def _categories():\n    return [{'id':'undeclared'}]\n"
        )
        fields = policies.model_dump(mode="json")
        fields["catalog"]["code_sha256"]["data/generator/dimensions.py"] = hashlib.sha256(
            (producer / "data/generator/dimensions.py").read_bytes()
        ).hexdigest()
        policies = DevelopmentPolicySet.model_validate_json(canonical_bytes(fields))
    else:

        def changed(*args):
            raise SnapshotError("campaign_generation_dirty_or_wrong_producer")

        monkeypatch.setattr(freeze, "_producer_pin", changed)
    with pytest.raises(SnapshotError):
        freeze.freeze_development_policies(
            tmp_path / "absent.json",
            policies=policies,
            evidence=ROOT / "docs/evidence",
            producer=producer,
        )
    assert not (tmp_path / "absent.json").exists()


@pytest.mark.parametrize("attack", ["digest", "policy", "context", "runtime"])
def test_frozen_original_policy_context_and_runtime_cannot_be_rebound(
    tmp_path, declared_catalog, monkeypatch, attack
):
    producer, policies, _ = declared_catalog
    output = tmp_path / "policies.json"
    result = freeze.freeze_development_policies(
        output, policies=policies, evidence=ROOT / "docs/evidence", producer=producer
    )
    raw = result.model_dump(mode="json")
    if attack == "digest":
        raw["policies_sha256"] = "0" * 64
    elif attack == "policy":
        raw["policies"]["seed_weights"]["137"] = 2
        # Even a rehashed document must remain bound to the original context.
        raw["policies_sha256"] = DevelopmentPolicySet.model_validate_json(
            canonical_bytes(raw["policies"])
        ).content_sha256()
    elif attack == "context":
        raw["context"]["use_case_quality_policy_sha256"]["anomaly"] = "0" * 64
    else:
        monkeypatch.setattr(
            freeze,
            "runtime_pin",
            lambda: result.context.runtime.model_copy(update={"code_sha256": "0" * 64}),
        )
        with pytest.raises(SnapshotError, match="frozen_runtime_changed"):
            freeze.load_frozen_development_policies(output)
        return
    with pytest.raises(ValidationError, match="actual_original_documents"):
        FrozenDevelopmentPolicies.model_validate_json(canonical_bytes(raw))
