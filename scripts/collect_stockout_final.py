"""Combine all six authorized public world receipts without reopening outcome archives."""

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.source_snapshot.files import read_bytes
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace
from retailops_ai.stockout_campaign.contract import CampaignFreeze, CampaignPermission
from retailops_ai.stockout_lifecycle.evidence import collect


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("freeze", "permission", "receipts", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--artifact-prefix")
    parser.add_argument("--workflow-run-id", type=int)
    args = parser.parse_args()
    try:
        freeze = CampaignFreeze.model_validate_json(
            read_bytes(args.freeze.parent, args.freeze.name)
        )
        permission = CampaignPermission.model_validate_json(
            read_bytes(args.permission.parent, args.permission.name)
        )
        roots: dict[tuple[str, int], Path] = {
            (s.world, s.seed): args.receipts / f"{s.world}-{s.seed}" for s in freeze.sources
        }
        if args.artifact_prefix is not None:
            if (
                not re.fullmatch(r"ai08-final-[0-9a-f]{40}-", args.artifact_prefix)
                or not args.workflow_run_id
                or args.workflow_run_id < 1
            ):
                raise ValueError("stockout_final_artifact_execution_identity_required")
            for s in freeze.sources:
                name = f"{s.world}-{s.seed}"
                root = args.receipts / (args.artifact_prefix + name)
                candidates = [p for p in (root, root / name) if (p / "native.json").is_file()]
                if len(candidates) != 1:
                    raise ValueError("stockout_final_downloaded_receipt_directory_ambiguous")
                roots[s.world, s.seed] = candidates[0]
        elif args.workflow_run_id is not None:
            raise ValueError("stockout_final_artifact_prefix_required_with_execution_identity")
        result = collect(
            roots,
            freeze=freeze,
            permission=permission,
        )
        if args.artifact_prefix is not None and any(
            w["resource"]["execution_commit"]
            != args.artifact_prefix.removeprefix("ai08-final-").removesuffix("-")
            or w["resource"]["workflow_run_id"] != args.workflow_run_id
            for w in result["execution_evidence"]["content"]["worlds"]
        ):
            raise ValueError("stockout_final_downloaded_receipts_wrong_execution")
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("stockout_final_collected_output_immutable")
        with tempfile.TemporaryDirectory(
            prefix=".stockout-final-collect-", dir=args.output.parent
        ) as temporary:
            root = Path(temporary) / "complete"
            root.mkdir(mode=0o700)
            files = {
                name + ".json": canonical_bytes(value) + b"\n" for name, value in result.items()
            }
            if sum(map(len, files.values())) > 16 * 1024**2:
                raise ValueError("stockout_final_collected_resource_limit")
            for name, raw in files.items():
                fd = os.open(
                    root / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            fsync_tree(root)
            publish_noreplace(root, args.output)
        print(
            json.dumps(
                dict(
                    status=result["final_quality"]["content"]["status"],
                    quality_id=result["final_quality"]["quality_id"],
                    execution_id=result["execution_evidence"]["evidence_id"],
                    model_refits=0,
                    ai08_ready=False,
                )
            )
        )
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps(dict(error="stockout_final_collection_failed", kind=type(error).__name__)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
