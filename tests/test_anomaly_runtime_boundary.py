"""A fresh runtime imports neither fitting machinery nor private evaluation replay."""

import subprocess
import sys


def test_private_batch_runtime_cannot_import_training_or_offline_quality_reader():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import retailops_ai.anomaly_portfolio.batch_cli; "
            "forbidden={'retailops_ai.anomaly_portfolio.training',"
            "'retailops_ai.anomaly_detectors.fit',"
            "'retailops_ai.anomaly_evaluation.verification',"
            "'retailops_ai.anomaly_evaluation.source_truth'}; "
            "assert not forbidden.intersection(sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
