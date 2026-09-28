import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from retailops_ai.cli import main
from retailops_ai.data_contracts.identity import canonical_sha256, logical_rows_sha256
from retailops_ai.data_contracts.registry import MAX_DOCUMENT_BYTES, MODELS, validate_document
from retailops_ai.data_contracts.run import transition_run

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts/intelligence/v1"
NEGATIVE = json.loads((CONTRACTS / "negative-cases.json").read_text())["cases"]
FORMATS = FormatChecker()


@FORMATS.checks("date-time", raises=ValueError)
def utc_format(value):
    if not isinstance(value, str):
        return True
    timestamp = datetime.fromisoformat(value)
    return timestamp.tzinfo is not None


def example(family):
    return json.loads((CONTRACTS / f"{family}.v1.example.json").read_text())


def schema(family):
    value = json.loads((CONTRACTS / f"{family}.v1.schema.json").read_text())
    Draft202012Validator.check_schema(value)
    return Draft202012Validator(value, format_checker=FORMATS)


def checked(family, value):
    return validate_document(family, json.dumps(value).encode())


def mutate(document, mutations):
    value = deepcopy(document)
    for mutation in mutations:
        parent = value
        for part in mutation["path"][:-1]:
            parent = parent[part]
        key = mutation["path"][-1]
        if mutation["op"] == "delete":
            del parent[key]
        else:
            parent[key] = deepcopy(mutation["value"])
    return value


@pytest.mark.parametrize("family", sorted(MODELS))
def test_reviewed_examples_pass_independent_schema_and_semantics(family):
    value = example(family)
    schema(family).validate(value)
    assert checked(family, value).model_dump(mode="json") == value


@pytest.mark.parametrize("case", NEGATIVE, ids=lambda case: case["name"])
def test_hand_authored_negative_cases_require_the_correct_layer(case):
    value = mutate(example(case["family"]), case["mutations"])
    structural_errors = list(schema(case["family"]).iter_errors(value))
    assert bool(structural_errors) == (case["layer"] == "schema")
    with pytest.raises(ValueError, match=case["error"]):
        checked(case["family"], value)


@pytest.mark.parametrize("family", sorted(MODELS))
def test_every_family_requires_exact_version_and_rejects_unknown_fields(family):
    for mutation in ({"schema_version": "1.1"}, {"extra": "ignored-by-old-consumer"}):
        value = {**example(family), **mutation}
        assert not schema(family).is_valid(value)
        with pytest.raises(ValidationError):
            checked(family, value)


def test_known_future_plan_and_missing_feature_are_explicit():
    value = example("feature")
    assert value["values"][2]["effective_date"] > value["key"]["forecast_origin"][:10]
    checked("feature", value)
    missing = value["values"][0]
    missing.update(status="missing", value=None, source_available_at=None, reason="missing_source")
    checked("feature", value)


def test_censored_label_remains_null_and_observed_zero_is_valid():
    bundle = example("bundle")
    assert bundle["labels"][0]["observed_sales_units"] == 0
    assert bundle["labels"][1]["observed_sales_units"] is None
    for label in bundle["labels"]:
        checked("label", label)


def test_boolean_flags_cannot_be_numbers():
    for family, path in (
        ("run", ["output_ref", "complete"]),
        ("dataset", ["readiness", "inventory_ready"]),
    ):
        value = mutate(
            example(family), [{"op": "set", "path": path, "value": int(family == "run")}]
        )
        with pytest.raises(ValueError, match="literal_boolean_required"):
            checked(family, value)


@pytest.mark.parametrize("path", ["/absolute", "a/../b", "./x", "a//b", "a\\b", "s3://bucket/x"])
def test_manifest_rejects_unsafe_paths(path):
    value = example("dataset")
    value["artifacts"][0]["path"] = path
    with pytest.raises(ValueError, match="unsafe_artifact_path"):
        checked("dataset", value)


def test_logical_identity_ignores_generation_paths_and_byte_representation():
    original = example("dataset")
    value = deepcopy(original)
    value["generated_at"] = "2026-09-02T00:00:00+00:00"
    value["artifacts"][0].update(
        path="other/partition.parquet", byte_sha256="b" * 64, size_bytes=256
    )
    assert checked("dataset", value).dataset_id == checked("dataset", original).dataset_id
    rows = [{"key": {"p": "a"}, "value": 0}, {"key": {"p": "b"}, "value": None}]
    assert logical_rows_sha256(rows, keys=["key"]) == logical_rows_sha256(rows[::-1], keys=["key"])
    assert canonical_sha256({"x": 1}) != canonical_sha256({"x": 1.0})
    assert canonical_sha256({"x": None}) != canonical_sha256({"x": 0})


@pytest.mark.parametrize(
    "path,value",
    [
        (["config", "seed"], 43),
        (["config", "scenario"], "different"),
        (["config", "calendar_version"], "calendar-utc-v2"),
        (["config", "resolved_parameters", "products"], 2),
        (["provenance", "commit_sha"], "2" * 40),
        (["provenance", "dependency_lock_sha256"], "b" * 64),
        (["logical_content_sha256"], "c" * 64),
        (["classification"], "simulation_truth"),
    ],
)
def test_semantic_changes_create_new_dataset_identity(path, value):
    original = example("dataset")
    changed = mutate(original["identity"], [{"op": "set", "path": path, "value": value}])
    identity = (
        MODELS["dataset"]
        .model_fields["identity"]
        .annotation.model_validate_json(json.dumps(changed))
    )
    assert identity.content_id() != original["dataset_id"]


def test_dirty_provenance_has_a_distinct_identity():
    value = example("dataset")
    value["identity"]["provenance"].update(code_state="dirty", dirty_patch_sha256="d" * 64)
    value["dataset_id"] = "source-sha256-" + canonical_sha256(value["identity"])
    checked("dataset", value)


@pytest.mark.parametrize(
    "rows,keys,error",
    [
        ([{"key": 1}, {"key": 1}], ["key"], "duplicate_logical_row_key"),
        ([{"key": None}], ["key"], "logical_row_key_missing"),
        ([{"other": 1}], ["key"], "logical_row_key_missing"),
        ([{"key": 1}], [], "logical_keys_required"),
    ],
)
def test_logical_hash_requires_complete_unique_stable_keys(rows, keys, error):
    with pytest.raises(ValueError, match=error):
        logical_rows_sha256(rows, keys=keys)


@pytest.mark.parametrize("constant", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_values_cannot_have_logical_identity(constant):
    with pytest.raises(ValueError, match="nonfinite_json_number"):
        canonical_sha256({"value": constant})
    raw = json.dumps({**example("feature"), "not_allowed": constant}).encode()
    with pytest.raises(ValueError, match="nonfinite_json_number"):
        validate_document("feature", raw)


def test_json_decoder_rejects_duplicate_names_and_large_inputs():
    with pytest.raises(ValueError, match="duplicate_json_field"):
        validate_document("label", b'{"schema_version":"1.0","schema_version":"2.0"}')
    with pytest.raises(ValueError, match="contract_document_too_large"):
        validate_document("bundle", b" " * (MAX_DOCUMENT_BYTES + 1))


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("missing-source", "missing_or_wrong_role_dataset"),
        ("missing-run", "prediction_manifest_run_missing"),
        ("failed-run", "prediction_run_model_mismatch"),
        ("feature-lineage", "record_dataset_lineage_mismatch"),
        ("logical-content", "bundle_logical_content_mismatch"),
        ("row-count", "bundle_record_count_mismatch"),
        ("duplicate-dataset", "duplicate_bundle_identity"),
        ("missing-training", "model_requires_succeeded_training_run_and_split"),
    ],
)
def test_closed_graph_rejects_individually_plausible_documents(mutation, error):
    value = example("bundle")
    if mutation == "missing-source":
        value["datasets"].pop(0)
    elif mutation == "missing-run":
        value["runs"].pop()
    elif mutation == "failed-run":
        run = value["runs"][-1]
        run.update(
            status="failed", output_ref=None, error={"code": "execution_failed", "retryable": False}
        )
    elif mutation == "feature-lineage":
        value["features"][0]["lineage"]["source_dataset_id"] = "source-sha256-" + "9" * 64
        for feature_value in value["features"][0]["values"]:
            feature_value["source_dataset_id"] = value["features"][0]["lineage"][
                "source_dataset_id"
            ]
        # Individual feature document is valid; the referenced source is absent from the bundle.
        checked("feature", value["features"][0])
        error = "missing_or_wrong_role_dataset"
    elif mutation == "logical-content":
        value["features"][0]["values"][0]["value"] = 1
        checked("feature", value["features"][0])
    elif mutation == "row-count":
        value["datasets"][2]["artifacts"][0]["rows"] = 3
        checked("dataset", value["datasets"][2])
    elif mutation == "duplicate-dataset":
        value["datasets"].append(deepcopy(value["datasets"][0]))
    else:
        value["runs"].pop(0)
    with pytest.raises(ValueError, match=error):
        checked("bundle", value)


@pytest.mark.parametrize(
    "cutoff,error",
    [
        ("2026-06-13T23:59:59Z", "model_cutoff_uses_test_or_immature_labels"),
        ("2026-06-14T23:59:59Z", "training_label_not_available_at_cutoff"),
    ],
)
def test_training_cutoff_requires_mature_and_available_labels(cutoff, error):
    value = example("bundle")
    # Keep a training-only graph so the assertion exercises cutoffs, not stale inference IDs.
    value.update(
        datasets=value["datasets"][:-1], runs=value["runs"][:1], predictions=[], tool_results=[]
    )
    model = value["models"][0]
    model["training_cutoff"] = cutoff
    model["model_id"] = "model-sha256-" + canonical_sha256(
        {k: v for k, v in model.items() if k != "model_id"}
    )
    value["runs"][0]["input_ref"]["as_of_time"] = cutoff
    value["runs"][0]["output_ref"]["artifact_id"] = model["model_id"]
    if error == "training_label_not_available_at_cutoff":
        value["labels"][0]["label_available_at"] = "2026-06-15T00:00:00Z"
        checked("label", value["labels"][0])
    checked("model", model)
    with pytest.raises(ValueError, match=error):
        checked("bundle", value)


def test_model_selection_must_finish_before_test_starts():
    value = example("bundle")
    value.update(
        datasets=value["datasets"][:-1], runs=value["runs"][:1], predictions=[], tool_results=[]
    )
    model = value["models"][0]
    model["selection_cutoff"] = "2026-08-04T00:00:00Z"
    model["model_id"] = "model-sha256-" + canonical_sha256(
        {k: v for k, v in model.items() if k != "model_id"}
    )
    value["runs"][0]["output_ref"]["artifact_id"] = model["model_id"]
    checked("model", model)
    with pytest.raises(ValueError, match="model_cutoff_uses_test_or_immature_labels"):
        checked("bundle", value)


@pytest.mark.parametrize(
    "role", ["source", "curated", "features", "labels", "split", "predictions"]
)
def test_each_dataset_role_recomputes_its_own_identity(role):
    value = next(d for d in example("bundle")["datasets"] if d["identity"]["role"] == role)
    original = value["dataset_id"]
    value["identity"]["config"]["seed"] += 1
    with pytest.raises(ValueError, match="dataset_identity_mismatch"):
        checked("dataset", value)
    value["dataset_id"] = role + "-sha256-" + canonical_sha256(value["identity"])
    assert checked("dataset", value).dataset_id != original


@pytest.mark.parametrize("mutation", ["grain", "date-range"])
def test_artifact_metadata_matches_full_forecast_grain_and_record_dates(mutation):
    value = example("bundle")
    artifact = value["datasets"][2]["artifacts"][0]
    if mutation == "grain":
        artifact["grain"].remove("forecast_origin")
        error = "forecast_artifact_grain_or_date_field_mismatch"
    else:
        artifact["date_to"] = "2026-08-31"
        checked("dataset", value["datasets"][2])
        error = "bundle_record_date_range_mismatch"
    with pytest.raises(ValueError, match=error):
        checked("bundle", value)


def test_prediction_generation_must_happen_during_its_run():
    value = example("bundle")
    value["predictions"][0]["generated_at"] = "2026-08-23T00:06:00Z"
    checked("prediction", value["predictions"][0])
    with pytest.raises(ValueError, match="prediction_generation_outside_run"):
        checked("bundle", value)


def test_truth_manifest_is_valid_metadata_but_never_a_feature_parent():
    value = example("bundle")
    source = value["datasets"][0]
    original = source["dataset_id"]
    source["identity"]["classification"] = source["classification"] = "simulation_truth"
    source["artifacts"][0]["classification"] = "simulation_truth"
    source["readiness"]["forecast"] = "not_ready"
    source["dataset_id"] = "source-sha256-" + canonical_sha256(source["identity"])
    checked("dataset", source)
    # Only replace the curated manifest to point at truth; others are checked after it.
    curated = value["datasets"][1]
    curated["identity"]["parents"]["source_dataset_id"] = source["dataset_id"]
    curated["dataset_id"] = "curated-sha256-" + canonical_sha256(curated["identity"])
    # A minimal closed graph is sufficient to check the classification boundary.
    feature = deepcopy(value["datasets"][2])
    feature["identity"]["parents"].update(
        source_dataset_id=source["dataset_id"], curated_dataset_id=curated["dataset_id"]
    )
    feature["dataset_id"] = "features-sha256-" + canonical_sha256(feature["identity"])
    value.update(
        datasets=[source, curated, feature],
        features=[],
        labels=[],
        splits=[],
        models=[],
        runs=[],
        predictions=[],
        tool_results=[],
    )
    assert original != source["dataset_id"]
    with pytest.raises(ValueError, match="training_input_is_not_observable_facts"):
        checked("bundle", value)


def state(status):
    value = example("run")
    value["status"] = status
    if status != "succeeded":
        value["output_ref"] = None
    if status in {"queued", "running"}:
        value["completed_at"] = None
    if status == "queued":
        value["started_at"] = None
    if status in {"failed", "cancelled"}:
        value["error"] = {
            "code": "cancelled" if status == "cancelled" else "execution_failed",
            "retryable": False,
        }
    return checked("run", value)


def test_run_transitions_pin_input_and_publish_only_complete_outputs():
    queued, running, succeeded = (state(s) for s in ("queued", "running", "succeeded"))
    assert transition_run(queued, running) == running
    assert transition_run(running, succeeded) == succeeded
    assert transition_run(succeeded, succeeded) == succeeded
    for terminal in ("failed", "cancelled"):
        assert transition_run(running, state(terminal)).status == terminal
    with pytest.raises(ValueError, match="illegal_run_transition"):
        transition_run(queued, succeeded)
    with pytest.raises(ValueError, match="illegal_run_transition"):
        transition_run(succeeded, running)
    changed = running.model_dump(mode="json")
    changed["requested_by"] = "different"
    with pytest.raises(ValueError, match="run_transition_changed_pinned_input"):
        transition_run(queued, checked("run", changed))


def test_tool_has_explicit_no_data_and_fixed_safe_errors():
    value = example("tool_result")
    value.update(
        status="no_data", items=[], source_ref=None, as_of=None, freshness_status="missing"
    )
    checked("tool_result", value)
    value.update(
        status="error",
        freshness_status="unavailable",
        error={
            "code": "unavailable",
            "description": "The required source is unavailable.",
            "retryable": True,
        },
    )
    checked("tool_result", value)
    value["error"]["retryable"] = False
    with pytest.raises(ValueError, match="tool_error_is_not_canonical"):
        checked("tool_result", value)


def test_tool_rejects_duplicate_predictions():
    value = example("tool_result")
    value["items"] *= 2
    with pytest.raises(ValueError, match="duplicate_tool_prediction"):
        checked("tool_result", value)


def test_contract_cli_is_offline_and_never_echoes_invalid_input(tmp_path, monkeypatch, capsys):
    import retailops_ai.config

    def forbidden(*args, **kwargs):
        raise AssertionError("contract-check must not read application settings")

    monkeypatch.setattr("retailops_ai.cli.load_settings", forbidden)
    monkeypatch.setattr(retailops_ai.config, "load_settings", forbidden)
    assert main(["contract-check", "bundle", str(CONTRACTS / "bundle.v1.example.json")]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "valid"
    path = tmp_path / "private-sentinel.json"
    path.write_text('{"schema_version":"private-sentinel"}')
    for family in ("label", "private-sentinel"):
        assert main(["contract-check", family, str(path)]) == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert json.loads(captured.err) == {"error": "invalid_contract_document"}
    assert main(["contract-check", "label", str(tmp_path / "absent")]) == 2
    capsys.readouterr()
    assert list(tmp_path.iterdir()) == [path]


def test_snapshot_gate_detects_intentionally_weakened_schema(tmp_path):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    command = [
        sys.executable,
        str(ROOT / "scripts/update_intelligence_contracts.py"),
        "--output",
        str(tmp_path),
    ]
    assert subprocess.run(command, env=env, capture_output=True, check=False).returncode == 0
    assert (
        subprocess.run([*command, "--check"], env=env, capture_output=True, check=False).returncode
        == 0
    )
    path = tmp_path / "feature.v1.schema.json"
    value = json.loads(path.read_text())
    value["additionalProperties"] = True
    value["properties"]["schema_version"].pop("const")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    result = subprocess.run(
        [*command, "--check"], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 1
    assert "feature.v1.schema.json" in result.stdout
