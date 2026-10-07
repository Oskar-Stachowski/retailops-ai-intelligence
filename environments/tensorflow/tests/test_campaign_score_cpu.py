"""Real three-family fits and fresh common scoring on controlled typed records only."""

import sys
from pathlib import Path
from time import perf_counter

from mlflow.tracking import MlflowClient
from test_campaign_fit_data import fit_plan
from test_campaign_fit_data import indexed as indexed
from test_campaign_score_data import score_indexed as score_indexed
from test_campaign_score_data import score_plan
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import campaign_fit_data as data
from retailops_ai.evaluation_campaign import campaign_fit_worker as fitting
from retailops_ai.evaluation_campaign import campaign_score_entry as scoring
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory, _verify_bundle_content
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitReceipt
from retailops_ai.evaluation_campaign.campaign_generation import _environment
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    FAMILIES,
    CampaignForecastRawPrediction,
)
from retailops_ai.evaluation_campaign.physical_forecast import _index


def test_real_fresh_six_model_cpu_inference_preserves_every_controlled_role_key(
    indexed, score_indexed, tmp_path
):
    train_db = indexed[0]
    score_db, _, manifest, population = score_indexed
    bundles, fits = {}, {}
    export_binding = {"scope": "controlled_typed_records_not_canonical_or_project_campaign"}
    export_digest = canonical_sha256(export_binding)
    initial = fit_plan()
    state = data.fit_encoding(train_db, initial)
    for index, family in enumerate(FAMILIES):
        plan = initial.model_copy(
            update={
                "family": family,
                "epochs": 2,
                "rf": initial.rf.model_copy(update={"n_estimators": 4, "max_depth": 3}),
                "hgb": initial.hgb.model_copy(update={"max_iter": 3, "min_samples_leaf": 1}),
            }
        )
        root = tmp_path / ("common-fit-" + family)
        root.mkdir(mode=0o700)
        for name in ("tmp", "bundle", "arrays"):
            (root / name).mkdir(mode=0o700)
        factory = data.tensorflow_matrices if family == "tensorflow" else data.tree_matrices
        matrices = {
            role: factory(train_db, role, state, plan, root / "arrays") for role in data.ROLES
        }
        write(root / "request.json", {"plan": plan.model_dump(mode="json")})
        write(
            root / "prepare.json", {"encoding_sha256": state.content_sha256(), "matrices": matrices}
        )
        write(root / "bundle/encoding.json", state.model_dump(mode="json"))
        write(root / "bundle/plan.json", plan.model_dump(mode="json"))
        write(
            root / "bundle/binding.json",
            {
                "export_receipt_sha256": export_digest,
                "dataset_id": manifest.dataset_id,
                "source_recipe_sha256": plan.source_recipe_sha256,
                "runtime_code_sha256": "d" * 64,
            },
        )
        for phase in ("fit", "reload"):
            measured = monitor(
                [sys.executable, "-I", "-B", str(Path(fitting.__file__)), phase, str(root)],
                root=root,
                log=root / (phase + ".log"),
                env=_environment(root),
                scratch=(root,),
                resources=plan.resources,
                deadline=perf_counter() + plan.resources.wall_seconds,
            )
            assert measured["status"] == "passed", (
                measured,
                (root / (phase + ".log")).read_text()[-5000:]
                if (root / (phase + ".log")).exists()
                else "preflight refused",
            )
        files, size = _bundle_inventory(root / "bundle", plan.max_artifact_bytes)
        validation = matrices["early_stopping"]
        receipt = CampaignForecastFitReceipt(
            protocol_sha256="b" * 64,
            operation_id="controlled-fit-" + family,
            reservation_id="campaign-operation-" + format(index + 1, "032x"),
            plan=plan,
            export_receipt_sha256=export_digest,
            dataset_id=manifest.dataset_id,
            runtime_code_sha256="d" * 64,
            train_keys_sha256=matrices["train"]["keys_sha256"],
            early_stopping_keys_sha256=validation["keys_sha256"],
            train_eligible_rows=state.train_rows,
            early_stopping_eligible_rows=validation.get("eligible_rows", validation.get("rows")),
            encoding_sha256=state.content_sha256(),
            model_artifact_sha256=canonical_sha256(files),
            model_artifact_bytes=size,
            artifact_files=files,
            worker_evidence={
                "scope": "controlled_native_fit",
                "fit": read(root / "fit.json"),
                "reload": read(root / "reload.json"),
            },
        )
        _verify_bundle_content(root / "bundle", receipt)
        bundles[family], fits[family] = root / "bundle", receipt
    root = tmp_path / "common-score"
    root.mkdir(mode=0o700)
    (root / "tmp").mkdir(mode=0o700)
    with _index(root / "score.sqlite", score_plan().max_index_bytes) as target:
        score_db.backup(target)
    write(root / "prepare.json", population)
    plan = score_plan()
    write(
        root / "request.json",
        {
            "plan": plan.model_dump(mode="json"),
            "bundles": {f: str(bundles[f]) for f in FAMILIES},
            "fits": {f: fits[f].model_dump(mode="json") for f in FAMILIES},
            "exported": export_binding,
        },
    )
    measured = monitor(
        [sys.executable, "-I", "-B", str(Path(scoring.__file__)), "predict", str(root)],
        root=root,
        log=root / "predict.log",
        env=_environment(root),
        scratch=(root,),
        resources=plan.resources,
        deadline=perf_counter() + plan.resources.wall_seconds,
    )
    assert measured["status"] == "passed", (
        measured,
        (root / "predict.log").read_text()[-5000:]
        if (root / "predict.log").exists()
        else "preflight refused",
    )
    result = read(root / "predict.json")
    assert result["all_models_share_all_role_keys"]
    assert all(
        result[k] == population[k]
        for k in ("rows", "eligible_rows", "keys_sha256", "eligible_keys_sha256")
    )
    predictions = [
        CampaignForecastRawPrediction.model_validate_json(line)
        for line in (root / "bundle/predictions.jsonl").read_bytes().splitlines()
    ]
    assert len(predictions) == population["rows"]
    assert sum(p.eligible for p in predictions) == population["eligible_rows"]
    assert all(p.values[3].median is None for p in predictions)
    assert all(
        p.values[5].mean is not None and p.values[5].median is not None
        for p in predictions
        if p.eligible
    )
    metrics = read(root / "bundle/metrics.json")["segments"][0]
    assert (
        metrics["rows"] == population["rows"]
        and metrics["eligible_rows"] == population["eligible_rows"]
    )
    assert metrics["models"]["tensorflow"]["mean"]["complete"]
    assert not metrics["models"]["rf_mean"]["median"]["complete"]
    run = MlflowClient(tracking_uri=(root / "tracking").as_uri()).get_run(result["mlflow_run_id"])
    assert run.info.status == "FINISHED" and run.data.params["plan_sha256"] == plan.content_sha256()
    assert (
        run.data.metrics["rows"] == population["rows"]
        and run.data.metrics["worker_peak_rss_bytes"] > 0
    )
