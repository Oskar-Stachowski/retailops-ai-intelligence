"""Read native code/environment metadata in the pinned producer interpreter."""

import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path


def main() -> None:
    source = Path(sys.argv[1])
    sys.path.insert(0, str(source))
    io = importlib.import_module("data.inventory.source_dataset_io")
    identity = importlib.import_module("data.generator.identity")
    provenance = identity.code_provenance(io.fingerprint())
    exporter = hashlib.sha256((source / "data/requirements-parquet.txt").read_bytes()).hexdigest()
    helper = Path(__file__).with_name("campaign_generation_worker.py")
    spec = importlib.util.spec_from_file_location("ai09_original_generation_worker", helper)
    if spec is None or spec.loader is None:
        raise ValueError("source_preparation_original_worker_unavailable")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    # Original native worker verifies every installed dependency, commit and code state.
    worker.producer_environment(
        source,
        {
            "producer_commit": sys.argv[2],
            "producer_lock_sha256": provenance["dependency_sha256"],
        },
        exporter,
    )
    print(
        json.dumps(
            {
                "producer_commit": provenance["git_commit"],
                "producer_lock_sha256": provenance["dependency_sha256"],
                "exporter_lock_sha256": exporter,
                "producer_planner_sha256": hashlib.sha256(
                    (source / "data/anomalies/development_plan.py").read_bytes()
                ).hexdigest(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
