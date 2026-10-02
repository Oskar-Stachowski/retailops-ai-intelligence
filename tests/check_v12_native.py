"""Local acceptance with the real pinned AI04 algorithm and explicit tiny transport fixtures."""

import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from test_v12_evidence import verifier_receipt
from test_v12_inference import approve_fixture, freshness_inputs, qualify_fixture, ready_export
from test_v12_runtime import COHORT, plan, runtime_fixture
from test_v12_runtime import artifacts as artifacts
from test_v12_runtime import prepared_input as prepared_input
from test_v12_runtime import tables as tables
from test_v12_runtime import timeline as timeline

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.forecast_jobs.v12_runtime import load_v12
from retailops_ai.forecasting.manifest_contract import FoldPlan
from retailops_ai.model_lifecycle import v12_evidence
from retailops_ai.model_lifecycle.v12_release import load_approved_v12

REPORT = Path(__file__).resolve().parents[1] / "reports/ai05-v12-native-runtime-smoke.json"


@pytest.fixture
def verifier_python():
    value = os.environ.get("AI04_VERIFIER_PYTHON")
    if not value:
        pytest.fail(
            "AI04_VERIFIER_PYTHON is required for this explicit local acceptance", pytrace=False
        )
    python = Path(value)
    if not python.is_absolute() or not python.is_file():
        pytest.fail("Absolute installed AI04 interpreter is required", pytrace=False)
    return python


GEN = r"""
import json,sys
from datetime import datetime,timedelta
import retailops_ai
from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes,canonical_sha256
from retailops_ai.forecasting.functional_recipe import Observation,empirical_baselines
from retailops_ai.forecasting.functional_v12_recipe import CohortObservation,fit_recipe_v12,bind_pooled_mean,merge_mean_calibration,PreparedV12Predictor
from retailops_ai.forecasting.functional_v12_campaign import campaign_code
from retailops_ai.forecasting.functional_v12_run import _input_schema
from retailops_ai.forecasting.functional_v12_cohort import observation_from_compact
from retailops_ai.forecasting.manifest_contract import FoldPlan
from retailops_ai.forecasting.features_contract import InputRow,HistoryContext
from retailops_ai.forecasting.quality_contract import QualityPolicy
from retailops_ai.forecasting.quality_metrics import volume_bin
request=json.loads(sys.stdin.buffer.read())
fold=FoldPlan.model_validate_json(canonical_bytes(request['fold']))
inputs=request['inputs']; cohort=request['cohort']
role=request.get('role','development_holdout')
first=InputRow.model_validate_json(canonical_bytes(inputs['rows'][0]))
values={v.name:v.value for v in first.values}
volume=volume_bin(values['rolling_mean_28'],QualityPolicy())
sample=[]
for day in sorted({fold.validation.start,fold.validation.end}):
    for i in range(120):
        origin=end_of_day(day).isoformat(); target=(day+timedelta(days=1)).isoformat()
        points={name+':'+functional:2.0 for name in ('history7','history28','weekday28') for functional in ('median','mean')}
        row=Observation(key=canonical_bytes([fold.name,'validation',origin,str(i),'unit-location','store',target]).decode(),fold=fold.name,role='validation',origin=origin,volume=volume,category=values['category_id'],channel='store',horizon=1,reasons=(),actual=15 if i%5==0 else 0,available=end_of_day(day+timedelta(days=2)).isoformat(),points=points,bands={name:(0.0,15.0) for name in ('history7','history28','weekday28')})
        sample.append(CohortObservation(row,cohort))
recipe=fit_recipe_v12(sample,fold)
recipe=bind_pooled_mean(recipe,merge_mean_calibration([recipe]))
refs={cohort:{fold.name:{'recipe_id':recipe['recipe_id']}}}
plan={'split_policy':{'folds':[request['fold']]},'method_policy':recipe['policy'],'required_dimensions':{'category':[values['category_id']],'channel':['store'],'volume':['zero','low','medium','high']}}
schema=_input_schema(plan,refs)
predictor=PreparedV12Predictor(recipe)
histories={h.content_sha256():h for h in [HistoryContext.model_validate_json(canonical_bytes(value)) for value in inputs['histories']]}
expected=[]
for value in inputs['rows']:
    row=InputRow.model_validate_json(canonical_bytes(value)); history=histories[row.history_context_sha256]
    points,bands=empirical_baselines(row,history); values={v.name:v.value for v in row.values}
    compact={'key':canonical_bytes([fold.name,role,row.forecast_origin.isoformat(),row.product_id,row.selling_location_id,row.channel,row.target_date.isoformat()]).decode(),'fold':fold.name,'role':role,'origin':row.forecast_origin.isoformat(),'volume':volume_bin(values['rolling_mean_28'],QualityPolicy()),'category':values['category_id'],'channel':row.channel,'horizon':row.horizon_days,'eligible':row.target_calendar_eligible,'reasons':[] if row.target_calendar_eligible else ['closed_target'],'actual':None,'label_available_at':None,'baseline_points':points,'baseline_bands':{name:list(band) if band is not None else None for name,band in bands.items()}}
    candidate,baseline,metadata=predictor.predict(observation_from_compact(compact,cohort))
    expected.append({'key':compact['key'],'candidate':candidate.model_dump(mode='json'),'baseline':baseline.model_dump(mode='json'),'metadata':metadata}|({'exclusion_reason':'closed_target'} if not row.target_calendar_eligible else {}))
print(json.dumps({'recipe':recipe,'code':campaign_code(),'signature_schema':schema,'expected':expected,'package_file':retailops_ai.__file__}))
"""


def test_real_pinned_predictor_matches_reference_without_touching_campaign(
    prepared_input, tmp_path, monkeypatch, verifier_python
):
    PYTHON = verifier_python
    original = plan()
    fold_raw = original.model_dump(mode="json")
    fold_raw["train"] = {
        "start": (original.train.start - timedelta(days=1)).isoformat(),
        "end": (original.train.end - timedelta(days=1)).isoformat(),
    }
    fold_raw["validation"]["start"] = (original.validation.start - timedelta(days=1)).isoformat()
    fold_raw["training_cutoff"] = (original.training_cutoff - timedelta(days=1)).isoformat()
    fold = FoldPlan.model_validate_json(canonical_bytes(fold_raw))
    completed = subprocess.run(
        [str(PYTHON), "-I", "-B", "-c", GEN],
        input=canonical_bytes(
            {
                "inputs": prepared_input.model_dump(mode="json"),
                "fold": fold.model_dump(mode="json"),
                "cohort": COHORT,
            }
        ),
        capture_output=True,
        check=False,
        timeout=60,
        env={"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"},
    )
    assert completed.returncode == 0, completed.stderr.decode()
    native = json.loads(completed.stdout)
    raw = prepared_input.model_dump(mode="json")
    raw["feature_manifest"]["descriptor"]["code"]["dependency_lock_sha256"] = native["code"][
        "dependency_lock_sha256"
    ]
    raw["feature_manifest"]["feature_set_id"] = "features-sha256-" + canonical_sha256(
        raw["feature_manifest"]["descriptor"]
    )
    raw["profile_id"] = "batch-profile-sha256-" + canonical_sha256(
        {key: value for key, value in raw.items() if key != "profile_id"}
    )
    inputs = PreparedInputs.model_validate_json(canonical_bytes(raw))
    root, run_id, recipe_id = runtime_fixture(
        tmp_path,
        inputs,
        recipe=native["recipe"],
        code=native["code"],
        signature_schema=native["signature_schema"],
        fold=fold,
    )
    monkeypatch.setattr(v12_evidence, "verify_with_wheel", lambda root, *_: verifier_receipt(root))
    loaded = load_v12(
        root, PYTHON, run_id=run_id, cohort_id=COHORT, fold=plan().name, recipe_id=recipe_id
    )
    first = loaded.predict(inputs)
    second = loaded.predict(inputs)
    assert [row.model_dump(mode="json") for row in first.predictions] == native["expected"]
    assert (
        first.predictions_sha256
        == second.predictions_sha256
        == canonical_sha256(native["expected"])
    )
    assert first.model_refits == 0 and first.serving_eligible is False
    report = {
        "status": "native_algorithm_load_predict_passed_on_explicit_fixture",
        "full_run_verifier": "explicit_transport_double_not_real_quality_evidence",
        "input_provenance": "explicit_test_fixture_with_pinned_lock_claim_not_real_source_acceptance",
        "active_campaign_read": False,
        "active_ai04_modified": False,
        "real_export_accepted": False,
        "fixture_fitting": "small_independent_invented_validation_rows_only",
        "forecast_rows": len(first.predictions),
        "original_predictor_match": True,
        "identical_repeat": True,
        "installed_ai04_package": native["package_file"],
        "code_sha256": native["code"]["code_sha256"],
        "dependency_lock_sha256": native["code"]["dependency_lock_sha256"],
        "predictions_sha256": first.predictions_sha256,
        "cold_load_seconds": first.cold_load_seconds,
        "compute_seconds": first.compute_seconds,
        "peak_rss_bytes": first.peak_rss_bytes,
        "prediction_model_refits": first.model_refits,
        "serving_eligible": False,
        "published_forecast_outputs": 0,
    }
    REPORT.write_bytes(canonical_bytes(report) + b"\n")


@pytest.mark.parametrize("closed", [False, True])
def test_real_pinned_inference_predictor_outside_holdout_after_explicit_fixture_review(
    prepared_input, tmp_path, monkeypatch, verifier_python, closed
):
    """Actual algorithm proof; full export/source/review remain explicit independent doubles."""
    original = plan()
    raw_fold = original.model_dump(mode="json")
    raw_fold["train"] = {
        "start": (original.train.start - timedelta(days=1)).isoformat(),
        "end": (original.train.end - timedelta(days=1)).isoformat(),
    }
    raw_fold["validation"]["start"] = (original.validation.start - timedelta(days=1)).isoformat()
    raw_fold["training_cutoff"] = (original.training_cutoff - timedelta(days=1)).isoformat()
    raw_fold["development_holdout"] = {
        "start": (original.development_holdout.start + timedelta(days=1)).isoformat(),
        "end": (original.development_holdout.end + timedelta(days=1)).isoformat(),
    }
    fold = FoldPlan.model_validate_json(canonical_bytes(raw_fold))
    base = freshness_inputs(prepared_input)
    if closed:
        raw = base.model_dump(mode="json")
        raw["rows"][0]["target_calendar_eligible"] = False
        next(v for v in raw["rows"][0]["values"] if v["name"] == "target_location_open")[
            "value"
        ] = False
        raw["profile_id"] = "batch-profile-sha256-" + canonical_sha256(
            {k: v for k, v in raw.items() if k != "profile_id"}
        )
        base = PreparedInputs.model_validate_json(canonical_bytes(raw))
    completed = subprocess.run(
        [str(verifier_python), "-I", "-B", "-c", GEN],
        input=canonical_bytes(
            dict(
                inputs=base.model_dump(mode="json"),
                fold=fold.model_dump(mode="json"),
                cohort=COHORT,
                role="inference",
            )
        ),
        capture_output=True,
        check=False,
        timeout=60,
        env={"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"},
    )
    assert completed.returncode == 0, completed.stderr.decode()
    native = json.loads(completed.stdout)
    raw = base.model_dump(mode="json")
    raw["feature_manifest"]["descriptor"]["code"]["dependency_lock_sha256"] = native["code"][
        "dependency_lock_sha256"
    ]
    raw["feature_manifest"]["feature_set_id"] = "features-sha256-" + canonical_sha256(
        raw["feature_manifest"]["descriptor"]
    )
    raw["profile_id"] = "batch-profile-sha256-" + canonical_sha256(
        {k: v for k, v in raw.items() if k != "profile_id"}
    )
    inputs = PreparedInputs.model_validate_json(canonical_bytes(raw))
    root, run_id, recipe_id = ready_export(
        tmp_path,
        inputs,
        recipe=native["recipe"],
        code=native["code"],
        signature_schema=native["signature_schema"],
        fold=fold,
    )
    monkeypatch.setattr(v12_evidence, "verify_with_wheel", lambda root, *_: verifier_receipt(root))
    loaded = load_v12(
        root, verifier_python, run_id=run_id, cohort_id=COHORT, fold=fold.name, recipe_id=recipe_id
    )
    assert inputs.as_of_time.date() < fold.development_holdout.start
    qualification = qualify_fixture(tmp_path, inputs, loaded, monkeypatch, transport=False)
    release_dir = approve_fixture(tmp_path, qualification, loaded)
    serving = load_approved_v12(
        release_dir,
        root,
        verifier_python,
        release_id=release_dir.name,
        image_digest="sha256:" + "b" * 64,
    )
    first, second = serving.predict(inputs), serving.predict(inputs)
    assert [p.model_dump(mode="json") for p in first.predictions] == native["expected"]
    assert first.predictions_sha256 == second.predictions_sha256
    if closed:
        excluded = first.predictions[0]
        assert excluded.exclusion_reason == "closed_target"
        for prediction in (excluded.candidate, excluded.baseline):
            assert prediction.median is prediction.mean is prediction.interval is None
    report = dict(
        status="native_inference_and_private_review_passed_on_explicit_fixture",
        full_run_verifier="explicit_transport_double_not_real_quality_evidence",
        input_provenance="explicit_source_reconstruction_double_not_real_source_acceptance",
        review="ten_explicit_fixture_reports_not_real_review",
        fixture_fitting="small_independent_invented_validation_rows_only",
        active_campaign_read=False,
        active_ai04_modified=False,
        real_export_accepted=False,
        real_model_approved=False,
        registered_in_mlflow=False,
        activated_as_champion=False,
        installed_ai04_package=native["package_file"],
        code_sha256=native["code"]["code_sha256"],
        dependency_lock_sha256=native["code"]["dependency_lock_sha256"],
        outside_development_holdout=True,
        forecast_rows=len(first.predictions),
        original_predictor_match=True,
        identical_repeat=True,
        predictions_sha256=first.predictions_sha256,
        cold_load_seconds=first.cold_load_seconds,
        compute_seconds=first.compute_seconds,
        peak_rss_bytes=first.peak_rss_bytes,
        model_refits=first.model_refits,
        published_forecast_outputs=first.published_forecast_outputs,
    )
    (
        REPORT.parent
        / (
            "ai05-v12-native-closed-calendar-smoke.json"
            if closed
            else "ai05-v12-native-inference-smoke.json"
        )
    ).write_bytes(canonical_bytes(report) + b"\n")
