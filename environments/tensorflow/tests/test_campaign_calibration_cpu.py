"""Fresh real calibration/MLflow worker on small role files and declared Tune metadata."""

import sys
from pathlib import Path
from time import perf_counter

from mlflow.tracking import MlflowClient
from test_campaign_calibration_data import calibration_control as calibration_control
from test_campaign_calibration_worker import request_control as request_control
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.evaluation_campaign import campaign_calibration_entry as entry
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibrationPlan,
)
from retailops_ai.evaluation_campaign.campaign_generation import _environment
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write


def test_fresh_calibration_cpu_retains_not_ready_and_private_mlflow_scope(
    request_control, tmp_path
):
    request, score = request_control
    root = tmp_path / "native-calibration"
    root.mkdir(mode=0o700)
    (root / "tmp").mkdir(mode=0o700)
    write(root / "request.json", request)
    plan = CampaignForecastCalibrationPlan.model_validate_json(
        __import__("json").dumps(request["plan"])
    )
    measured = monitor(
        [sys.executable, "-I", "-B", str(Path(entry.__file__)), str(root)],
        root=root,
        log=root / "calibrate.log",
        env=_environment(root),
        scratch=(root,),
        resources=plan.resources,
        deadline=perf_counter() + plan.resources.wall_seconds,
    )
    assert measured["status"] == "passed", (
        str(measured)
        + "\n"
        + (
            (root / "calibrate.log").read_text()[-5000:]
            if (root / "calibrate.log").exists()
            else "preflight refused"
        )
    )
    result = read(root / "calibrate.json")
    assert result["rows"] == score.rows and result["eligible_rows"] == score.eligible_rows
    assert result["calibration"]["center"] == request["tune"]["selection"]["median"]
    assert (
        result["calibration"]["status"] == "not_ready"
        and not result["calibration"]["calibration_fitted"]
    )
    assert len(result["calibration"]["horizons"]) == 14
    assert result["full_calibration_label_passes"] == 1
    assert result["tune_label_passes"] == result["independent_or_final_label_passes"] == 0
    assert not result["architecture_reselected"]
    assert result["worker_peak_rss_bytes"] > 0 and result["worker_cpu_seconds"] > 0
    client = MlflowClient(tracking_uri=(root / "tracking").as_uri())
    run = client.get_run(result["mlflow_run_id"])
    assert run.info.status == "FINISHED"
    assert (
        run.data.params["role"] == "calibration"
        and run.data.params["calibration_fitted"] == "False"
    )
    assert run.data.params["plan_sha256"] == plan.content_sha256()
    assert run.info.artifact_uri.startswith((root / "tracking-artifacts").as_uri())
    assert {
        item.path for item in client.list_artifacts(result["mlflow_run_id"], "calibration")
    } == {
        "calibration/plan.json",
        "calibration/parents.json",
        "calibration/population.json",
        "calibration/calibration.json",
    }
