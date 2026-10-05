"""Reconstruct complete public parents, DQ and qualified inputs without truth access."""

import argparse
import hashlib
import json
import resource
import time
from pathlib import Path

from retailops_ai.anomaly_portfolio.inputs import verified_features
from retailops_ai.curated.builder import build_curated
from retailops_ai.full_raw_dq.store import build_replay
from retailops_ai.qualified_anomalies.store import build
from retailops_ai.source_snapshot.files import canonical_json, read_bytes
from retailops_ai.source_snapshot.importer import import_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--generated-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    bundle = json.loads(read_bytes(args.source_bundle.parent, args.source_bundle.name, 1024**2))
    if bundle["status"] != "passed":
        raise ValueError("anomaly_portfolio_public_parent_unqualified")
    started = time.monotonic()

    def mark(stage: str) -> None:
        print(
            json.dumps({"stage": stage, "elapsed_seconds": round(time.monotonic() - started, 3)}),
            flush=True,
        )

    root = args.generated_root.absolute()
    source = import_snapshot(Path(bundle["public"]), root, required_use_cases=("anomaly_source",))
    if source.snapshot.source_id != bundle["source_dataset_id"]:
        raise ValueError("anomaly_portfolio_bundle_source_mismatch")
    mark("import")
    curated = build_curated(source.directory, root)
    mark("curated")
    capture_digest = hashlib.sha256(
        read_bytes(Path(bundle["capture"]), "raw/events.jsonl", 32 * 1024**2)
    ).hexdigest()
    capture = root / "public-capture" / bundle["source_dataset_id"] / capture_digest
    capture.mkdir(parents=True, exist_ok=True)
    for name in ("raw/events.jsonl", "source_binding.json"):
        raw = read_bytes(Path(bundle["capture"]), name, 32 * 1024**2)
        destination = capture / name
        destination.parent.mkdir(exist_ok=True, parents=True)
        if destination.exists():
            if destination.read_bytes() != raw:
                raise ValueError("anomaly_portfolio_capture_immutable_conflict")
        else:
            destination.write_bytes(raw)
    replay = build_replay(capture, curated.directory, source.directory, root)
    mark("full_dq")
    parents = (
        Path(replay["directory"]),
        Path(bundle["coverage"]),
        curated.directory,
        source.directory,
    )
    result = build(*parents, root)
    mark("qualified_features")
    features = verified_features(Path(result["directory"]), *parents)
    mark("independent_verification")
    receipt = {
        "status": "passed",
        "source_dataset_id": source.snapshot.source_id,
        "feature_dir": str(features.directory),
        "feature_id": features.manifest.qualified_anomaly_input_id,
        "feature_manifest_sha256": features.manifest_sha256,
        "feature_rows_sha256": features.manifest.descriptor.rows_sha256,
        "feature_rows": features.manifest.descriptor.row_count,
        "feature_status_counts": features.manifest.descriptor.status_counts,
        "parents": [str(p) for p in parents],
        "snapshot_id": source.snapshot.snapshot_id,
        "truth_access": "excluded",
        "source_bundle_sha256": hashlib.sha256(args.source_bundle.read_bytes()).hexdigest(),
        "elapsed_seconds": time.monotonic() - started,
        "peak_rss_native": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "model_quality": "not_evaluated",
        "final_test": "not_opened",
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_bytes(canonical_json(receipt) + b"\n")
    print(json.dumps(receipt), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
