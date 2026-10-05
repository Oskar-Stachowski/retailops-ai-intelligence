"""Retain verified features only when refreshed producer public inputs are byte-identical."""

import argparse
import hashlib
from pathlib import Path

from retailops_ai.anomaly_portfolio.artifacts import immutable_json
from retailops_ai.source_snapshot.files import decode_json, read_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--original-bundle", type=Path, required=True)
    parser.add_argument("--refreshed-bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt_raw = read_bytes(args.receipt.parent, args.receipt.name, 1024**2)
    old_raw = read_bytes(args.original_bundle.parent, args.original_bundle.name, 1024**2)
    new_raw = read_bytes(args.refreshed_bundle.parent, args.refreshed_bundle.name, 1024**2)
    receipt, old, new = map(decode_json, (receipt_raw, old_raw, new_raw))
    if (
        receipt["status"] != "passed"
        or receipt["truth_access"] != "excluded"
        or receipt["final_test"] != "not_opened"
        or receipt["source_bundle_sha256"] != hashlib.sha256(old_raw).hexdigest()
        or new["status"] != "passed"
        or any(old[k] != new[k] for k in ("source_dataset_id", "public", "coverage"))
    ):
        raise ValueError("anomaly_preparation_rebind_parent_mismatch")
    hashes = {}
    for name in ("raw/events.jsonl", "source_binding.json"):
        before = read_bytes(Path(old["capture"]), name, 32 * 1024**2)
        after = read_bytes(Path(new["capture"]), name, 32 * 1024**2)
        if before != after:
            raise ValueError("anomaly_preparation_rebind_public_inputs_changed")
        hashes[name] = hashlib.sha256(after).hexdigest()
    immutable_json(
        args.output,
        {
            **receipt,
            "source_bundle_sha256": hashlib.sha256(new_raw).hexdigest(),
            "public_input_rebind": {
                "original_receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
                "original_bundle_sha256": hashlib.sha256(old_raw).hexdigest(),
                "refreshed_capture": new["capture"],
                "public_files_sha256": hashes,
                "feature_content": "unchanged_original_native_verification_retained",
            },
        },
    )
    print("anomaly_preparation_byte_identical_rebind=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
