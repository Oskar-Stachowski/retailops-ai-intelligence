"""Keep batch queue contracts aligned with strict runtime validators."""

import argparse
import json
from pathlib import Path

from retailops_ai.forecast_jobs.contracts import (
    BatchRequest,
    BatchRun,
    MechanicsOutput,
    MechanicsProfile,
    QueuePolicy,
)
from retailops_ai.forecast_jobs.execution_contracts import (
    ExecutionLimits,
    RuntimeExecution,
    RuntimeResult,
)
from retailops_ai.forecast_jobs.input_store import RegisteredInputs, RegistrationLimits
from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.forecast_jobs.publication import OutputManifest, Partition, Publication
from retailops_ai.forecast_jobs.runtime import RuntimePin

ROOT = Path(__file__).resolve().parents[1] / "contracts/forecast_jobs/v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, model in (
        ("request", BatchRequest),
        ("run", BatchRun),
        ("mechanics_profile", MechanicsProfile),
        ("mechanics_output", MechanicsOutput),
        ("policy", QueuePolicy),
        ("prepared_inputs", PreparedInputs),
        ("runtime_pin", RuntimePin),
        ("registered_inputs", RegisteredInputs),
        ("registration_limits", RegistrationLimits),
        ("execution_limits", ExecutionLimits),
        ("runtime_execution", RuntimeExecution),
        ("runtime_result", RuntimeResult),
        ("output_manifest", OutputManifest),
        ("output_partition", Partition),
        ("publication", Publication),
    ):
        expected = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        path = ROOT / (name + ".schema.json")
        if args.check:
            if not path.is_file() or path.read_text() != expected:
                print("forecast_jobs_contract_snapshot_mismatch: " + name)
                return 1
        else:
            ROOT.mkdir(parents=True, exist_ok=True)
            path.write_text(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
