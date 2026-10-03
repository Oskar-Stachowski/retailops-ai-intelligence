"""Local owner exceptions cannot qualify other runs or enter the normal namespace."""

from copy import deepcopy
from pathlib import Path

import pytest
from test_v12_inference import artifacts as artifacts
from test_v12_inference import inputs as inputs
from test_v12_inference import loaded as loaded
from test_v12_inference import prepared_input as prepared_input
from test_v12_inference import qualification as qualification
from test_v12_inference import tables as tables
from test_v12_inference import timeline as timeline

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs.v12_contracts import V12RuntimePin
from retailops_ai.forecast_jobs.v12_executor import (
    ACCEPTED_MANIFEST,
    ACCEPTED_RUN,
    development_acceptance_matches,
)
from retailops_ai.forecast_jobs.v12_runtime import validate_inputs
from retailops_ai.model_lifecycle.v12_development import (
    V12DevelopmentAcceptance,
    read_development_acceptance,
)
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    MODEL,
    TEST_MODEL,
    model_namespace,
)
from retailops_ai.model_lifecycle.v12_release_contracts import V12Qualification

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def acceptance():
    return read_development_acceptance(ROOT / "docs/evidence/04-v12-acceptance.json")


def development_pin(pin):
    """Only a pin-contract double; this does not fake full archive verification."""
    raw = pin.model_dump(mode="json")
    raw.update(run_id=ACCEPTED_RUN, forecast_model_status="not_ready")
    raw["manifest"]["sha256"] = ACCEPTED_MANIFEST
    return V12RuntimePin.model_validate_json(canonical_bytes(raw))


def test_verified_owner_record_is_unchanged_and_no_production_permission(acceptance, tmp_path):
    source = ROOT / "docs/evidence/04-v12-acceptance.json"
    before = source.read_bytes()
    assert acceptance.scope == "local_development_only"
    assert acceptance.production_deployment_authorized is False
    assert acceptance.original_quality_reclassified is False
    copied = tmp_path / "changed-owner-record.json"
    copied.write_bytes(before + b" ")
    with pytest.raises(ValueError, match="owner_decision_changed"):
        read_development_acceptance(copied)
    assert source.read_bytes() == before


@pytest.mark.parametrize(
    "field", ["production_deployment_authorized", "original_quality_reclassified"]
)
def test_false_flags_are_not_numeric_or_production_authorization(acceptance, field):
    for value in (True, 0, "false"):
        raw = acceptance.model_dump(mode="json")
        raw[field] = value
        assert not development_acceptance_matches(raw)
        with pytest.raises(ValueError):
            V12DevelopmentAcceptance.model_validate_json(canonical_bytes(raw))


@pytest.mark.parametrize("change", ["run", "manifest", "status"])
def test_acceptance_is_bound_to_exact_original_not_ready_run(acceptance, loaded, change):
    pin = development_pin(loaded.pin)
    acceptance.verify_pin(pin)
    raw = pin.model_dump(mode="json")
    if change == "run":
        raw["run_id"] = "functional-v12-run-sha256-" + "a" * 64
    elif change == "manifest":
        raw["manifest"]["sha256"] = "a" * 64
    else:
        raw["forecast_model_status"] = "ready"
    changed = V12RuntimePin.model_validate_json(canonical_bytes(raw))
    with pytest.raises(ValueError, match="wrong_run"):
        acceptance.verify_pin(changed)


def test_input_gates_remain_and_development_probe_keeps_original_quality(
    acceptance, loaded, inputs, qualification
):
    qualification = V12Qualification.model_validate_json(
        (qualification / "qualification.json").read_bytes()
    )
    pin = development_pin(loaded.pin)
    policy = qualification.source_policy
    with pytest.raises(ValueError, match="parent_policy"):
        validate_inputs(pin, inputs, source_policy=policy)
    validate_inputs(pin, inputs, source_policy=policy, development_acceptance=acceptance)
    raw = qualification.model_dump(mode="json")
    raw["pin"] = pin.model_dump(mode="json")
    raw["development_acceptance"] = acceptance.model_dump(mode="json")
    raw["qualification_id"] = "v12-qualification-sha256-" + canonical_sha256(
        {key: value for key, value in raw.items() if key != "qualification_id"}
    )
    result = V12Qualification.model_validate_json(canonical_bytes(raw))
    assert result.pin.forecast_model_status == "not_ready"
    assert not result.serving_eligible
    changed = deepcopy(raw)
    changed.pop("development_acceptance")
    changed["qualification_id"] = "v12-qualification-sha256-" + canonical_sha256(
        {key: value for key, value in changed.items() if key != "qualification_id"}
    )
    with pytest.raises(ValueError, match="readiness"):
        V12Qualification.model_validate_json(canonical_bytes(changed))
    with pytest.raises(ValueError, match="parent_policy"):
        validate_inputs(
            pin,
            inputs.model_copy(update={"schema_version": "1.0", "source_freshness": None}),
            source_policy=policy,
            development_acceptance=acceptance,
        )


def test_explicit_development_namespace_and_default_reader_stay_separate():
    assert model_namespace("local") == MODEL
    assert model_namespace("test", mechanics=True) == TEST_MODEL
    assert model_namespace("local", development=True) == "retailops-demand-forecast-v12-development"
    for environment, options in (
        ("production", {"development": True}),
        ("local", {"mechanics": True}),
        ("test", {"development": True, "mechanics": True}),
    ):
        with pytest.raises(ValueError, match="namespace_environment"):
            model_namespace(environment, **options)
