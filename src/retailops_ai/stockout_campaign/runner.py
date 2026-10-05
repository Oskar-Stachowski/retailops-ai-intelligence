"""One authorized frozen world; retain a complete report even when quality fails."""

import argparse
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.source_snapshot.files import decode_json, file_hash, relative_path
from retailops_ai.source_snapshot.publish import publish_noreplace
from retailops_ai.stockout_campaign.archive import partition_inputs, restore
from retailops_ai.stockout_campaign.assembly import assemble_final, guard
from retailops_ai.stockout_campaign.contract import CampaignFreeze, CampaignPermission, SourceRef
from retailops_ai.stockout_campaign.download import download
from retailops_ai.stockout_campaign.evaluation import bound_recipes, evaluate_final
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe

MAX_PUBLIC = 4 * 1024**2


def event(
    path: Path,
    freeze: CampaignFreeze,
    permission: CampaignPermission,
    source: SourceRef,
    status: str,
) -> None:
    record = dict(
        at=datetime.now(UTC).isoformat(),
        campaign_id=freeze.campaign_id,
        source_dataset_id=source.source_dataset_id,
        world=source.world,
        seed=source.seed,
        permission_sha256=canonical_sha256(permission.model_dump(mode="json")),
        approved_by=permission.approved_by,
        status=status,
        model_refits=0,
    )
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "ab") as output:
        output.write(canonical_bytes(record) + b"\n")
        output.flush()
        os.fsync(output.fileno())


def unpack(archive: Path, source: SourceRef, staging: Path) -> tuple[Path, dict[str, Any]]:
    if file_hash(archive.parent, archive.name) != (source.artifact_bytes, source.artifact_sha256):
        raise ValueError("stockout_final_artifact_archive_changed")
    with zipfile.ZipFile(archive) as zip:
        entries = zip.infolist()
        names = [relative_path(ref.filename).as_posix() for ref in entries]
        if (
            len(entries) > 80
            or len(set(names)) != len(names)
            or not {"checkpoint.json", "resource.json", "parents.tar.gz"} <= set(names)
        ):
            raise ValueError("stockout_final_zip_inventory")
        if any(
            ref.is_dir() or stat.S_IFMT(ref.external_attr >> 16) not in {0, stat.S_IFREG}
            for ref in entries
        ):
            raise ValueError("stockout_final_zip_unsafe_member")
        if (
            zip.getinfo("checkpoint.json").file_size > MAX_PUBLIC
            or zip.getinfo("resource.json").file_size > MAX_PUBLIC
        ):
            raise ValueError("stockout_final_public_metadata_limit")
        raw = zip.read("checkpoint.json")
        if hashlib.sha256(raw).hexdigest() != source.checkpoint_sha256:
            raise ValueError("stockout_final_checkpoint_changed")
        checkpoint = decode_json(raw, limit=MAX_PUBLIC)
        resource_raw = zip.read("resource.json")
        if (
            hashlib.sha256(resource_raw).hexdigest() != source.resource_sha256
            or checkpoint["resource_receipt_sha256"] != source.resource_sha256
        ):
            raise ValueError("stockout_final_resource_receipt_changed")
        resource = decode_json(resource_raw, limit=MAX_PUBLIC)
        plan = checkpoint["plan"]
        producer = resource["producer"]
        consumer = resource["consumer"]
        if (
            plan["seed"] != source.seed
            or plan["producer_commit"] != source.producer_commit
            or plan["consumer_commit"] != source.consumer_commit
            or producer["source_dataset_id"] != source.source_dataset_id
            or producer["qualification_id"] != source.qualification_id
            or any(
                consumer["ids"][k] != getattr(source, k)
                for k in (
                    "feature_bundle_id",
                    "upstream_bundle_id",
                    "label_bundle_id",
                    "temporal_bundle_id",
                )
            )
            or consumer["final_test_membership"] != source.eligible_test_membership
        ):
            raise ValueError("stockout_final_downloaded_world_binding")
        ref = zip.getinfo("parents.tar.gz")
        if ref.file_size != checkpoint["parents"]["archive_bytes"] or ref.file_size > 512 * 1024**2:
            raise ValueError("stockout_final_parent_archive_size")
        target = staging / "parents.tar.gz"
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with zip.open(ref) as stream, os.fdopen(fd, "wb") as output:
            count = 0
            while chunk := stream.read(1024**2):
                count += len(chunk)
                if count > ref.file_size:
                    raise ValueError("stockout_final_parent_expansion")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if count != ref.file_size:
            raise ValueError("stockout_final_parent_archive_truncated")
    return target, checkpoint


def run_world(
    *,
    freeze: CampaignFreeze,
    permission: CampaignPermission | None,
    source: SourceRef,
    recipe: ScoringRecipe,
    policy: ScoringPolicy,
    archive: Path,
    report: Path,
    audit: Path,
) -> dict[str, Any]:
    guard(freeze, permission, source)
    bound_recipes(freeze, recipe, policy)
    if permission is None:
        raise ValueError("stockout_final_owner_permission_required")
    if report.exists() or report.is_symlink():
        raise ValueError("stockout_final_report_immutable")
    event(audit, freeze, permission, source, "authorized_before_private_read")
    try:
        if not archive.exists():
            download(source, archive)
        with tempfile.TemporaryDirectory(
            prefix=".stockout-final-world-", dir=report.parent
        ) as temporary:
            root = Path(temporary)
            tar, checkpoint = unpack(archive, source, root)
            roots = restore(tar, checkpoint, root / "restored")
            inputs = partition_inputs(roots)
            data = assemble_final(
                roots["temporal"], inputs, freeze=freeze, permission=permission, source=source
            )
            result = evaluate_final(
                data,
                freeze=freeze,
                permission=permission,
                source=source,
                recipe=recipe,
                policy=policy,
            )
            raw = canonical_bytes(result) + b"\n"
            if len(raw) > 4 * 1024**2:
                raise ValueError("stockout_final_report_resource_limit")
            staged_report = root / "complete-report.json"
            fd = os.open(staged_report, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            publish_noreplace(staged_report, report)
        event(audit, freeze, permission, source, "complete_" + result["status"])
        return result
    except BaseException:
        event(audit, freeze, permission, source, "failed_no_quality_acceptance")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("freeze", "permission", "recipe", "policy", "archive", "report", "audit"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--world", choices=["matching", "future_stress"], required=True)
    parser.add_argument("--seed", type=int, choices=[42, 137, 2026], required=True)
    args = parser.parse_args()
    try:
        freeze = CampaignFreeze.model_validate_json(args.freeze.read_bytes())
        permission = CampaignPermission.model_validate_json(args.permission.read_bytes())
        source = next(s for s in freeze.sources if (s.world, s.seed) == (args.world, args.seed))
        result = run_world(
            freeze=freeze,
            permission=permission,
            source=source,
            recipe=ScoringRecipe.model_validate_json(args.recipe.read_bytes()),
            policy=ScoringPolicy.model_validate_json(args.policy.read_bytes()),
            archive=args.archive,
            report=args.report,
            audit=args.audit,
        )
        print(
            json.dumps(
                dict(
                    world=args.world,
                    seed=args.seed,
                    status=result["status"],
                    model_refits=0,
                    ai08_ready=False,
                )
            )
        )
        return 0
    except (ValueError, OSError, KeyError, StopIteration, zipfile.BadZipFile) as error:
        # Signed redirect URLs and bearer credentials never enter error output.
        print(json.dumps(dict(error="stockout_final_world_failed", kind=type(error).__name__)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
