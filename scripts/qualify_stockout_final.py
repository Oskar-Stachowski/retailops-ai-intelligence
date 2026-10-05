"""Qualify a passed six-world campaign using a full public-input replay on its runner."""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import pyarrow.parquet as parquet  # type: ignore[import-untyped]
from pydantic import TypeAdapter

from retailops_ai.data_contracts.common import UtcTime
from retailops_ai.source_snapshot.files import read_bytes, read_json
from retailops_ai.stockout_campaign.archive import restore
from retailops_ai.stockout_campaign.assembly import guard
from retailops_ai.stockout_campaign.contract import CampaignFreeze, CampaignPermission
from retailops_ai.stockout_campaign.runner import unpack
from retailops_ai.stockout_lifecycle.evidence import LIMITS, collect
from retailops_ai.stockout_lifecycle.qualification import qualify_final
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_runtime.scope import PhysicalScope


def receipt_roots(
    root: Path, freeze: CampaignFreeze, execution_commit: str | None = None
) -> dict[tuple[str, int], Path]:
    """Require exactly this run's six retained world artifact directories."""
    commit = execution_commit or os.environ["GITHUB_SHA"]
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("stockout_final_qualification_invalid_execution_commit")
    result: dict[tuple[str, int], Path] = {}
    for source in freeze.sources:
        name = f"{source.world}-{source.seed}"
        artifact = root / f"ai08-final-{commit}-{name}"
        candidates = [p for p in (artifact, artifact / name) if (p / "native.json").is_file()]
        if len(candidates) != 1:
            raise ValueError("stockout_final_qualification_receipt_directory_ambiguous")
        result[source.world, source.seed] = candidates[0]
    return result


def smoke_scope(temporal: Path, freeze: CampaignFreeze) -> tuple[PhysicalScope, UtcTime]:
    """Choose an origin with an eligible membership, without reading any outcome columns."""
    manifest = read_json(temporal, "manifest.json")
    origin = None
    products: set[str] = set()
    for spec in manifest["descriptor"]["partitions"]:
        reference = next(ref for ref in spec["files"] if ref["role"] == "membership")
        rows = parquet.read_table(
            temporal / reference["path"],
            columns=["product_id", "stock_location_id", "as_of", "role", "eligible"],
        ).to_pylist()
        for row in rows:
            if origin is None and row["role"] == "test" and row["eligible"]:
                origin = row["as_of"]
            if origin is not None and row["as_of"] == origin:
                products.add(row["product_id"])
    if origin is None or not products:
        raise ValueError("stockout_final_qualification_no_eligible_membership_origin")
    scope = PhysicalScope(
        product_ids=tuple(sorted(products)[:20]),
        stock_location_ids=freeze.expected_stock_locations,
    )
    return scope, TypeAdapter(UtcTime).validate_python(origin)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("freeze", "permission", "recipe", "policy", "receipts", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--final-execution-commit", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--final-run-id", type=int, default=os.environ.get("GITHUB_RUN_ID"))
    args = parser.parse_args()
    if args.final_run_id is None or args.final_run_id <= 0:
        raise ValueError("stockout_final_qualification_invalid_execution_run")
    if (
        sys.platform != "linux"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("GITHUB_REPOSITORY") != "Oskar-Stachowski/retailops-ai-intelligence"
        or shutil.disk_usage(args.output.parent).free < LIMITS["minimum_free_bytes"] + 640 * 1024**2
    ):
        raise ValueError("stockout_final_qualification_requires_owned_remote_runner_reserve")
    freeze = CampaignFreeze.model_validate_json(args.freeze.read_bytes())
    permission = CampaignPermission.model_validate_json(args.permission.read_bytes())
    source = next(s for s in freeze.sources if (s.world, s.seed) == ("matching", 42))
    guard(freeze, permission, source)
    roots = receipt_roots(args.receipts, freeze, args.final_execution_commit)
    result = collect(roots, freeze=freeze, permission=permission)
    if result["final_quality"]["content"]["status"] != "passed":
        raise ValueError("stockout_final_quality_not_ready_no_qualification")
    if any(
        w["resource"]["execution_commit"] != args.final_execution_commit
        or w["resource"]["workflow_run_id"] != args.final_run_id
        for w in result["execution_evidence"]["content"]["worlds"]
    ):
        raise ValueError("stockout_final_qualification_wrong_execution")
    archive = roots["matching", 42] / "source.zip"
    with tempfile.TemporaryDirectory(prefix=".stockout-final-qualification-") as temp:
        staging = Path(temp)
        tar, checkpoint = unpack(archive, source, staging)
        parents = restore(tar, checkpoint, staging / "restored")
        selection_path = staging / "selected" / "selection.json"
        selection_path.parent.mkdir(mode=0o700)
        with zipfile.ZipFile(archive) as zip:
            if zip.getinfo("selection.json").file_size > 4 * 1024**2:
                raise ValueError("stockout_final_qualification_selection_byte_limit")
            selection_path.write_bytes(zip.read("selection.json"))
            selection_path.chmod(0o600)
        scope, origin = smoke_scope(parents["temporal"], freeze)
        output = qualify_final(
            freeze=freeze,
            permission=permission,
            recipe=ScoringRecipe.model_validate_json(args.recipe.read_bytes()),
            policy=ScoringPolicy.model_validate_json(args.policy.read_bytes()),
            receipt_roots=roots,
            selection_path=selection_path,
            curated=parents["curated"],
            features=parents["features"],
            upstream=parents["upstream"],
            scope=scope,
            as_of=origin,
            output=args.output,
        )
    q = json.loads(read_bytes(output, "qualification.json"))
    print(
        json.dumps(
            dict(
                status="qualified",
                qualification_id=q["qualification_id"],
                final_campaign_id=q["final_campaign_id"],
                smoke_rows=q["smoke_rows"],
                complete_public_parent_replay=True,
                model_refits=0,
                model_promoted=False,
                ai08_ready=False,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
