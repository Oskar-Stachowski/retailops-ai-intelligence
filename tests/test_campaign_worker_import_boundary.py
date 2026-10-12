"""Fresh workers must not retain the orchestration graph before loading ML runtimes."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from retailops_ai.evaluation_campaign import campaign_fit_artifact


@pytest.mark.parametrize("worker", ["campaign_score_worker", "campaign_context_bundle_worker"])
def test_fresh_worker_import_does_not_load_campaign_controllers(worker):
    package_root = Path(campaign_fit_artifact.__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys,importlib,json; sys.path.insert(0,sys.argv[1]); "
            "importlib.import_module('retailops_ai.evaluation_campaign.'+sys.argv[2]); "
            "print(json.dumps(sorted(sys.modules)))",
            str(package_root),
            worker,
        ],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    modules = set(json.loads(result.stdout))
    assert "retailops_ai.evaluation_campaign.campaign_fit_artifact" in modules
    assert not modules.intersection(
        {
            "retailops_ai.evaluation_campaign.campaign_fit",
            "retailops_ai.evaluation_campaign.campaign_export",
            "retailops_ai.evaluation_campaign.campaign_robust_receipt",
            "retailops_ai.evaluation_campaign.canonical_qualification",
            "tensorflow",
            "mlflow",
        }
    )
