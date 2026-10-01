"""Offline runtime boundaries use explicit export/transport doubles, never quality fixtures."""

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import plan
from test_forecast_manifests import timeline as timeline
from test_forecast_runtime import prepared_input as source_inputs
from test_v12_evidence import verifier_receipt, write_fixture

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs import v12_runtime as runtime
from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.forecast_jobs.supervisor import ExecutionError
from retailops_ai.forecast_jobs.v12_contracts import (
    V12Execution,
    V12ExecutionLimits,
    V12Forecast,
    V12RuntimePin,
    V12RuntimeResult,
)
from retailops_ai.model_lifecycle import v12_evidence
from retailops_ai.source_snapshot.files import file_hash

ROOT = Path(__file__).resolve().parents[1]
COHORT = "cohort-unit-runtime"
INPUT_CACHE = pytest.StashKey[PreparedInputs]()


@pytest.fixture
def prepared_input(request):
    # Build one independent source fixture; each case receives revalidated immutable inputs.
    if INPUT_CACHE not in request.config.stash:
        request.config.stash[INPUT_CACHE] = source_inputs.__wrapped__(
            request.getfixturevalue("artifacts"), request.getfixturevalue("timeline")
        )
    return PreparedInputs.model_validate_json(request.config.stash[INPUT_CACHE].model_dump_json())


def runtime_fixture(tmp_path, inputs, *, recipe=None, code=None, signature_schema=None, fold=None):
    """Mapping fixture only: native full-run verification is explicitly doubled in unit tests."""
    root = write_fixture(tmp_path / "export")
    fold = fold or plan()
    body = recipe or {
        "fold": fold.name,
        "selection_cutoff": fold.selection_cutoff.isoformat(),
        "policy": {"mean_variant": "additive"},
        "support": {"cohort_ids": [COHORT]},
    }
    if "recipe_id" not in body:
        body = {**body, "recipe_id": "functional-v12-recipe-sha256-" + canonical_sha256(body)}
    path = f"campaign/recipes/{COHORT}/{fold.name}.json"
    (root / path).parent.mkdir(parents=True)
    (root / path).write_bytes(canonical_bytes(body) + b"\n")
    size, digest = file_hash(root, path)
    parent = inputs.feature_manifest.descriptor.parent
    card = json.loads((root / "model_card.json").read_text())
    card.update(
        recipes={
            COHORT: {
                fold.name: {
                    "artifact": {
                        "path": path,
                        "recipe_id": body["recipe_id"],
                        "size_bytes": size,
                        "sha256": digest,
                    },
                    "selection_cutoff": body["selection_cutoff"],
                }
            }
        },
        cohort_lineage={
            COHORT: {
                "source_dataset_id": parent.source_dataset_id,
                "snapshot_id": parent.snapshot_id,
            }
        },
    )
    (root / "model_card.json").write_bytes(canonical_bytes(card) + b"\n")
    freeze = json.loads((root / "campaign/freeze.json").read_text())
    freeze["descriptor"].update(
        method_policy=body["policy"], split_policy={"folds": [fold.model_dump(mode="json")]}
    )
    (root / "campaign/freeze.json").write_bytes(canonical_bytes(freeze) + b"\n")
    signature = json.loads((root / "signature.json").read_text())
    schema = signature_schema or {"type": "object"}
    signature.update(input_schema=schema, input_schema_sha256=canonical_sha256(schema))
    (root / "signature.json").write_bytes(canonical_bytes(signature) + b"\n")
    manifest = json.loads((root / "run_manifest.json").read_text())
    descriptor = manifest["descriptor"]
    descriptor["code"] = code or {
        "code_sha256": "e" * 64,
        "dependency_lock_sha256": inputs.feature_manifest.descriptor.code.dependency_lock_sha256,
    }
    descriptor["files"][path] = {}
    for name in descriptor["files"]:
        size, digest = file_hash(root, name)
        descriptor["files"][name] = {"size_bytes": size, "sha256": digest}
    descriptor["bytes"] = sum(ref["size_bytes"] for ref in descriptor["files"].values())
    manifest["run_id"] = "functional-v12-run-sha256-" + canonical_sha256(descriptor)
    (root / "run_manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
    return root, manifest["run_id"], body["recipe_id"]


@pytest.fixture
def loaded(tmp_path, prepared_input, monkeypatch):
    root, run_id, recipe_id = runtime_fixture(tmp_path, prepared_input)
    monkeypatch.setattr(v12_evidence, "verify_with_wheel", lambda root, *_: verifier_receipt(root))
    return runtime.load_v12(
        root,
        Path(sys.executable),
        run_id=run_id,
        cohort_id=COHORT,
        fold=plan().name,
        recipe_id=recipe_id,
    )


def result(loaded, inputs):
    predictions = [
        {
            "key": runtime.prediction_key(loaded.pin, row),
            "candidate": {"median": 2.0, "mean": 3.0, "interval": {"lower": 0.0, "upper": 10.0}},
            "baseline": {"median": 2.0, "mean": 2.0, "interval": {"lower": 0.0, "upper": 10.0}},
            "metadata": {
                "selected": "global",
                "baseline": "global",
                "mean_source": "global",
                "exact_reference_median": True,
                "exact_reference_interval": True,
                "recipe_id": loaded.pin.recipe_id,
            },
        }
        for row in inputs.rows
    ]
    return V12RuntimeResult.model_validate_json(
        canonical_bytes(
            dict(
                pin=loaded.pin.model_dump(mode="json"),
                profile_id=inputs.profile_id,
                predictions=predictions,
                predictions_sha256=canonical_sha256(predictions),
                cold_load_seconds=0.01,
                compute_seconds=0.01,
                peak_rss_bytes=1024**2,
                generated_at=datetime.now(UTC).isoformat(),
                model_refits=0,
                source_generation=False,
                serving_eligible=False,
                published_forecast_outputs=0,
            )
        )
    )


def replace_child(monkeypatch, code):
    original = subprocess.Popen
    children = []

    def spawn(command, **kwargs):
        assert command[1:3] == ["-I", "-B"]
        assert command[3].endswith("/forecast_jobs/v12_executor.py")
        assert set(kwargs["env"]) == {
            "OPENBLAS_NUM_THREADS",
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "TMPDIR",
        }
        child = original([sys.executable, "-I", "-B", "-c", code], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    return children


def test_recipe_is_selected_by_all_explicit_pins_and_import_never_promotes(loaded):
    assert loaded.pin.cohort_id == COHORT and loaded.pin.fold == plan()
    assert loaded.pin.forecast_model_status == "not_ready"
    assert loaded.pin.serving_eligible is False


@pytest.mark.parametrize("field", ["run_id", "cohort_id", "fold", "recipe_id"])
def test_wrong_explicit_pin_is_rejected(loaded, field):
    values = dict(
        run_id=loaded.pin.run_id, cohort_id=COHORT, fold=plan().name, recipe_id=loaded.pin.recipe_id
    )
    values[field] = "wrong-pin"
    with pytest.raises((ValueError, KeyError)):
        runtime.load_v12(loaded.root, loaded.python, **values)


def test_complete_result_keeps_dual_targets_and_strips_credentials(
    loaded, prepared_input, monkeypatch
):
    expected = result(loaded, prepared_input)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unit-private-marker")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    children = replace_child(
        monkeypatch,
        "import os,sys; assert 'AWS_SECRET_ACCESS_KEY' not in os.environ; assert 'PYTHONPATH' not in os.environ; sys.stdout.buffer.write("
        + repr(expected.model_dump_json().encode())
        + ")",
    )
    actual = loaded.predict(prepared_input)
    assert actual == expected
    assert actual.predictions[0].candidate.mean != actual.predictions[0].baseline.mean
    assert actual.published_forecast_outputs == 0 and actual.model_refits == 0
    assert len(children) == 1 and children[0].poll() == 0


@pytest.mark.parametrize("change", ["count", "profile", "pin", "memory"])
def test_child_result_must_match_exact_request(loaded, prepared_input, monkeypatch, change):
    raw = result(loaded, prepared_input).model_dump(mode="json")
    if change == "count":
        raw["predictions"] = raw["predictions"][:-1]
        raw["predictions_sha256"] = canonical_sha256(raw["predictions"])
    elif change == "profile":
        raw["profile_id"] = "batch-profile-sha256-" + "d" * 64
    elif change == "pin":
        raw["pin"]["source_dataset_id"] = "source-sha256-" + "d" * 64
    else:
        raw["peak_rss_bytes"] = 2 * 1024**3
    replace_child(
        monkeypatch, "import sys; sys.stdout.buffer.write(" + repr(canonical_bytes(raw)) + ")"
    )
    with pytest.raises(ExecutionError, match="pin_or_count"):
        loaded.predict(prepared_input)


@pytest.mark.parametrize(
    "code, marker",
    [
        ("import time; time.sleep(5)", "wall_limit"),
        ("import sys; sys.stdout.buffer.write(b'x'*1100000)", "output_limit"),
        ("import sys; sys.stderr.buffer.write(b'x'*20000)", "output_limit"),
        ("raise SystemExit(1)", "child_failed"),
    ],
)
def test_child_failure_limits_and_process_reaping(
    loaded, prepared_input, monkeypatch, code, marker
):
    children = replace_child(monkeypatch, code)
    with pytest.raises(ExecutionError, match=marker):
        loaded.predict(prepared_input, limits=V12ExecutionLimits(wall_seconds=0.3))
    assert len(children) == 1 and children[0].poll() is not None


def test_changed_selected_artifact_is_rejected_before_child(loaded, prepared_input, monkeypatch):
    (loaded.root / loaded.pin.recipe_path).write_text("{}")
    children = replace_child(monkeypatch, "raise AssertionError('never execute')")
    with pytest.raises(ExecutionError, match="artifact_changed"):
        loaded.predict(prepared_input)
    assert children == []


@pytest.mark.parametrize("change", ["source", "snapshot", "lock", "policy", "origin"])
def test_incompatible_input_parent_and_origin_are_rejected(loaded, prepared_input, change):
    raw = loaded.pin.model_dump(mode="json")
    if change == "origin":
        raw["fold"]["selection_cutoff"] = prepared_input.as_of_time.isoformat()
        # A malformed cutoff is refused directly by FoldPlan's own chronology.
        with pytest.raises(ValueError):
            loaded.pin.model_validate_json(canonical_bytes(raw))
        return
    if change == "source":
        raw["source_dataset_id"] = "source-sha256-" + "d" * 64
    elif change == "snapshot":
        raw["snapshot_id"] = "snapshot-sha256-" + "a" * 64
    elif change == "lock":
        raw["dependency_lock_sha256"] = "a" * 64
    else:
        content = prepared_input.model_dump(mode="json")
        content["feature_manifest"]["descriptor"]["resolved_policy"][
            "maximum_observation_age_days"
        ] = 4
        content["feature_manifest"]["descriptor"]["requested_policy"][
            "maximum_observation_age_days"
        ] = 4
        content["feature_manifest"]["feature_set_id"] = "features-sha256-" + canonical_sha256(
            content["feature_manifest"]["descriptor"]
        )
        content["profile_id"] = "batch-profile-sha256-" + canonical_sha256(
            {key: value for key, value in content.items() if key != "profile_id"}
        )
        prepared_input = PreparedInputs.model_validate_json(canonical_bytes(content))
    pin = loaded.pin.model_validate_json(canonical_bytes(raw))
    with pytest.raises(ValueError, match="input_parent_policy_or_budget"):
        runtime.validate_inputs(pin, prepared_input)


@pytest.mark.parametrize("change", ["labels", "future_reference", "history_binding"])
def test_inference_inputs_cannot_add_labels_or_future_information(prepared_input, change):
    raw = prepared_input.model_dump(mode="json")
    if change == "labels":
        raw["rows"][0]["actual"] = 42
    elif change == "future_reference":
        raw["rows"][0]["values"][0]["source_available_at"] = (
            prepared_input.as_of_time + timedelta(seconds=1)
        ).isoformat()
    else:
        raw["rows"][0]["history_context_sha256"] = "a" * 64
    with pytest.raises(ValueError):
        PreparedInputs.model_validate_json(canonical_bytes(raw))


def test_current_editable_interpreter_is_refused_by_real_child(loaded, prepared_input):
    with pytest.raises(ExecutionError, match="child_failed"):
        loaded.predict(prepared_input)


def test_mean_can_be_outside_quantiles_and_missing_outputs_remain_null():
    forecast = V12Forecast.model_validate_json(
        canonical_bytes(dict(median=2.0, mean=100.0, interval=dict(lower=0.0, upper=10.0)))
    )
    assert forecast.mean > forecast.interval.upper
    missing = V12Forecast(median=None, mean=None, interval=None)
    assert missing.model_dump(mode="json") == dict(median=None, mean=None, interval=None)


@pytest.mark.parametrize(
    "name, model",
    [
        ("runtime_pin", V12RuntimePin),
        ("execution", V12Execution),
        ("runtime_result", V12RuntimeResult),
    ],
)
def test_versioned_offline_schema_snapshots_match_contracts(name, model):
    assert (
        ROOT / "contracts/forecast/v12_offline" / (name + ".schema.json")
    ).read_bytes() == canonical_bytes(model.model_json_schema()) + b"\n"


@pytest.mark.parametrize("change", ["negative", "nonfinite", "wrong_interval", "median_outside"])
def test_invalid_forecast_values_are_rejected(change):
    raw = dict(median=2.0, mean=3.0, interval=dict(lower=0.0, upper=10.0))
    if change == "negative":
        raw["mean"] = -1.0
    elif change == "nonfinite":
        raw["mean"] = float("inf")
    elif change == "wrong_interval":
        raw["interval"] = dict(lower=20.0, upper=10.0)
    else:
        raw["median"] = 11.0
    with pytest.raises(ValueError):
        V12Forecast.model_validate(raw)


def test_input_origin_must_belong_to_selected_fold(loaded, prepared_input):
    raw = loaded.pin.model_dump(mode="json")
    raw["fold"]["development_holdout"] = {
        "start": (loaded.pin.fold.development_holdout.start + timedelta(days=1)).isoformat(),
        "end": (loaded.pin.fold.development_holdout.end + timedelta(days=1)).isoformat(),
    }
    pin = loaded.pin.model_validate_json(canonical_bytes(raw))
    with pytest.raises(ValueError, match="origin_outside_bound_holdout"):
        runtime.validate_inputs(pin, prepared_input)


def test_request_byte_budget_is_checked_before_starting_child(loaded, prepared_input, monkeypatch):
    monkeypatch.setattr(runtime, "MAX_REQUEST_BYTES", 2)
    children = replace_child(monkeypatch, "raise AssertionError('never execute')")
    with pytest.raises(ExecutionError, match="request_limit"):
        loaded.predict(prepared_input)
    assert children == []


def test_live_memory_budget_reaps_child(loaded, prepared_input, monkeypatch):
    monkeypatch.setattr(runtime, "tree_rss", lambda *_: 2 * 1024**3)
    children = replace_child(monkeypatch, "import time; time.sleep(5)")
    with pytest.raises(ExecutionError, match="memory_limit"):
        loaded.predict(prepared_input)
    assert len(children) == 1 and children[0].poll() is not None


def test_unsupported_hgb_recipe_is_refused_without_fallback(tmp_path, prepared_input, monkeypatch):
    recipe = {
        "fold": plan().name,
        "selection_cutoff": plan().selection_cutoff.isoformat(),
        "policy": {"mean_variant": "hgb_blend"},
        "support": {"cohort_ids": [COHORT]},
    }
    root, run_id, recipe_id = runtime_fixture(tmp_path, prepared_input, recipe=recipe)
    monkeypatch.setattr(v12_evidence, "verify_with_wheel", lambda root, *_: verifier_receipt(root))
    with pytest.raises(ValueError, match="hgb_input_not_supported"):
        runtime.load_v12(
            root,
            Path(sys.executable),
            run_id=run_id,
            cohort_id=COHORT,
            fold=plan().name,
            recipe_id=recipe_id,
        )


def installed_double(tmp_path, loaded):
    """Test-only installed predictor double, shared by offline/inference boundary tests."""
    environment = tmp_path / "operator-test-wheel"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True)
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site = environment / "lib" / version / "site-packages"
    # Test-only installation: real shared typed contracts plus explicitly named predictor doubles.
    package = site / "retailops_ai"
    shutil.copytree(
        ROOT / "src/retailops_ai", package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    (site / "test-dependencies.pth").write_text(
        str(Path(sys.prefix) / "lib" / version / "site-packages") + "\n"
    )
    code = {
        "code_sha256": loaded.pin.code_sha256,
        "dependency_lock_sha256": loaded.pin.dependency_lock_sha256,
    }
    (package / "forecasting/functional_v12_campaign.py").write_text(
        "def campaign_code():\n    return " + repr(code) + "\n"
    )
    (package / "forecasting/functional_v12_cohort.py").write_text(
        "def observation_from_compact(row, cohort_id):\n    assert row['actual'] is None and row['label_available_at'] is None\n    return row\n"
    )
    (package / "forecasting/functional_recipe.py").write_text(
        "def empirical_baselines(row, history):\n"
        "    from statistics import mean,median\n"
        "    sample=[p.observed_units for p in history.points if p.observed_units is not None]\n"
        "    return ({name+':median':float(median(sample)) for name in ('history7','history28','weekday28')} | {name+':mean':float(mean(sample)) for name in ('history7','history28','weekday28')}, {name:(float(min(sample)),float(max(sample))) for name in ('history7','history28','weekday28')})\n"
    )
    (package / "forecasting/functional_v12_recipe.py").write_text(
        "class Forecast:\n"
        "    def __init__(self,value): self.value=value\n"
        "    def model_dump(self,mode): return self.value\n"
        "class PreparedV12Predictor:\n"
        "    def __init__(self,recipe): self.recipe=recipe\n"
        "    def predict(self,row):\n"
        "        import os\n"
        "        assert not any(key.startswith(('AWS_','DATABASE_','MLFLOW_','API_')) for key in os.environ)\n"
        "        assert row['actual'] is None and row['label_available_at'] is None\n"
        "        base={'median':row['baseline_points']['history28:median'],'mean':row['baseline_points']['history28:mean'],'interval':dict(zip(('lower','upper'),row['baseline_bands']['history28']))}\n"
        "        return Forecast(base | {'mean':base['mean']+1}),Forecast(base),{'selected':None,'baseline':None,'mean_source':'unit_double','exact_reference_median':True,'exact_reference_interval':True,'recipe_id':self.recipe['recipe_id']}\n"
    )
    installed = runtime.LoadedV12Forecast(loaded.root, environment / "bin/python", loaded.pin)
    return installed


def test_installed_child_boundary_uses_explicit_v12_predictor_double(
    loaded, prepared_input, tmp_path
):
    """Portable subprocess test; the real AI04 predictor is separately checked with its wheel."""
    installed = installed_double(tmp_path, loaded)
    before = file_hash(loaded.root, loaded.pin.recipe_path)
    first = installed.predict(prepared_input)
    second = installed.predict(prepared_input)
    assert first.predictions_sha256 == second.predictions_sha256
    assert len(first.predictions) == len(prepared_input.rows) == 14
    assert first.predictions[0].candidate.mean == first.predictions[0].baseline.mean + 1
    assert first.model_refits == 0 and first.serving_eligible is False
    assert file_hash(loaded.root, loaded.pin.recipe_path) == before
    assert not list((tmp_path / "operator-test-wheel").rglob("*.pyc"))
