"""Owned GitHub development replay of an existing source; never evaluate final outcomes."""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.source_snapshot.files import read_json
from retailops_ai.stockout_campaign.archive import restore
from retailops_ai.stockout_campaign.contract import CampaignFreeze
from retailops_ai.stockout_campaign.download import REPOSITORY, download
from retailops_ai.stockout_campaign.runner import unpack
from retailops_ai.stockout_policy.comparison import compare_capacities
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_temporal_series.bundle import assemble_partitioned_development
from retailops_ai.stockout_temporal_storage.store import PartitionInputs


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("freeze", "recipe", "policy", "request", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    request = read_json(args.request.parent, args.request.name)
    if (
        sys.platform != "linux"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or request["version"] != "stockout-capacity-comparison-request-2.0.0"
        or request["owner_authorized"] is not True
        or request["capacities"] != [0.2, 0.4, 0.5]
        or request["final_test_outcomes_evaluated"] is not False
        or not request["authorization_evidence"]
        or shutil.disk_usage(args.output.parent).free < 6 * 1024**3 + 640 * 1024**2
    ):
        raise ValueError("stockout_capacity_owned_development_request_required")
    freeze = CampaignFreeze.model_validate_json(args.freeze.read_bytes())
    recipe = ScoringRecipe.model_validate_json(args.recipe.read_bytes())
    policy = ScoringPolicy.model_validate_json(args.policy.read_bytes())
    if request["selection_id"] != freeze.selection_id:
        raise ValueError("stockout_capacity_frozen_selection_request_changed")
    source = next(s for s in freeze.sources if (s.world, s.seed) == ("matching", 42))
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("stockout_capacity_output_must_be_new")
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(
        prefix=".stockout-development-", dir=args.output.parent
    ) as temp:
        root = Path(temp)
        archive = root / "source.zip"
        download(source, archive)
        tar, checkpoint = unpack(archive, source, root)
        with zipfile.ZipFile(archive) as zip:
            if zip.getinfo("selection.json").file_size > 4 * 1024**2:
                raise ValueError("stockout_capacity_selection_byte_limit")
            selection = json.loads(zip.read("selection.json"))
        roots = restore(tar, checkpoint, root / "restored")
        data = assemble_partitioned_development(
            roots["temporal"],
            PartitionInputs(
                roots["curated"],
                roots["private_import"],
                roots["features"],
                roots["upstream"],
                roots["labels"],
            ),
            allow_evaluation_truth=True,
        )
        result = compare_capacities(
            data, selection=selection, freeze=freeze, recipe=recipe, policy=policy
        )
        args.output.mkdir(mode=0o700)
        for name, document in (
            ("comparison.json", result),
            ("scoring-policy.json", result["scoring_policy"]),
            ("selection.json", selection),
            (
                "execution.json",
                dict(
                    version="stockout-capacity-execution-2.0.0",
                    status="passed",
                    workflow_run_id=os.environ["GITHUB_RUN_ID"],
                    commit=os.environ["GITHUB_SHA"],
                    wall_seconds=time.perf_counter() - started,
                    owner_request=request,
                    complete_public_parent_replay=True,
                    source_artifact_sha256=source.artifact_sha256,
                    final_test_outcomes_evaluated=False,
                    model_refits=0,
                    model_promoted=False,
                ),
            ),
        ):
            path = args.output / name
            path.write_bytes(canonical_bytes(document) + b"\n")
            path.chmod(0o600)
    print("Frozen development capacity comparison retained; no final outcomes evaluated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
