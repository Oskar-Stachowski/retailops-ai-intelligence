"""Reuse an exact, still-valid genuine qualification; never restore or evaluate labels."""

import argparse
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from recover_stockout_final_receipts import api, check_request, extract

from retailops_ai.source_snapshot.files import inventory, read_bytes
from retailops_ai.stockout_campaign.contract import CampaignFreeze
from retailops_ai.stockout_campaign.download import download
from retailops_ai.stockout_campaign.evaluation import bound_recipes
from retailops_ai.stockout_lifecycle.contract import StockoutQualification
from retailops_ai.stockout_lifecycle.release import receipt
from retailops_ai.stockout_public_inputs import implementation
from retailops_ai.stockout_runtime.inputs import PreparedStockoutInputs

FILES = {
    "campaign_freeze.json",
    "campaign_permission.json",
    "execution_evidence.json",
    "final_quality.json",
    "inputs.json",
    "model_card.json",
    "policy.json",
    "qualification.json",
    "recipe.json",
    "selection.json",
    "signature.json",
    "smoke.json",
}


def verify(root: Path, request: dict[str, Any], freeze: CampaignFreeze) -> StockoutQualification:
    inventory(root, FILES)
    q = StockoutQualification.model_validate_json(read_bytes(root, "qualification.json"))
    reference = request["qualified_artifact"]
    inputs = PreparedStockoutInputs.model_validate_json(read_bytes(root, "inputs.json"))
    now = datetime.now(UTC)
    if (
        q.qualification_id != reference["qualification_id"]
        or q.final_campaign_id != freeze.campaign_id
        or q.purpose != "qualified_stockout"
        or q.quality_status != "passed_independent_final_campaign"
        or not q.created_at <= now < q.valid_until
        or inputs.preparation_code_sha256 != implementation()
        or inputs.inputs_id != json.loads(read_bytes(root, "model_card.json"))["smoke_inputs_id"]
    ):
        raise ValueError("final_qualification_reuse_identity_expiry_or_adapter")
    bound_recipes(freeze, q.recipe, q.policy)
    for name, expected in [
        ("inputs", q.public_inputs),
        ("model_card", q.model_card),
        ("final_quality", q.final_quality),
        ("smoke", q.smoke),
        ("signature", q.signature),
    ]:
        if receipt(read_bytes(root, name + ".json")) != expected:
            raise ValueError("final_qualification_reuse_receipt_changed")
    for name in ("final_quality", "execution_evidence"):
        if (
            hashlib.sha256(read_bytes(root, name + ".json")).hexdigest()
            != request[name + "_sha256"]
        ):
            raise ValueError("final_qualification_reuse_final_evidence_changed")
    return q


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("GITHUB_REPOSITORY") != "Oskar-Stachowski/retailops-ai-intelligence"
        or shutil.disk_usage(args.output.parent).free < 6 * 1024**3
    ):
        raise ValueError("final_qualification_reuse_owned_runner_reserve_required")
    request = json.loads(args.request.read_bytes())
    freeze = CampaignFreeze.model_validate_json(args.freeze.read_bytes())
    original = api("actions/runs/" + str(request["final_run_id"]))
    jobs = api("actions/runs/" + str(request["final_run_id"]) + "/jobs?per_page=100")["jobs"]
    check_request(request, freeze, original, jobs)
    ref = request["qualified_artifact"]
    run = api("actions/runs/" + str(ref["run_id"]))
    job = api("actions/jobs/" + str(ref["job_id"]))
    qualified = [
        s
        for s in job["steps"]
        if s["name"] == "Qualify existing passed campaign with the public format adapter"
    ]
    if (
        run["head_sha"] != ref["execution_commit"]
        or job["run_id"] != ref["run_id"]
        or len(qualified) != 1
        or qualified[0]["conclusion"] != "success"
        or ref["bytes"] > 16 * 1024**2
    ):
        raise ValueError("final_qualification_reuse_successful_qualifier_required")
    metadata = api("actions/artifacts/" + str(ref["id"]))
    if (
        metadata["name"] != ref["name"]
        or metadata["digest"] != "sha256:" + ref["sha256"]
        or metadata["expired"] is not False
    ):
        raise ValueError("final_qualification_reuse_artifact_metadata_changed")
    args.output.mkdir(mode=0o700)
    archive = args.output / "qualification.zip"
    transport = freeze.sources[0].model_copy(
        update=dict(
            workflow_run_id=ref["run_id"],
            artifact_id=ref["id"],
            artifact_bytes=ref["bytes"],
            artifact_sha256=ref["sha256"],
        )
    )
    download(transport, archive)
    extract(archive, args.output, FILES)
    archive.unlink()
    root = args.output / ref["qualification_id"]
    inventory(args.output, {ref["qualification_id"] + "/" + name for name in FILES})
    q = verify(root, request, freeze)
    print(
        json.dumps(
            dict(
                status="genuine_qualification_reused",
                qualification_id=q.qualification_id,
                smoke_rows=q.smoke_rows,
                full_parent_replay_already_verified=True,
                source_restores=0,
                final_evaluations=0,
                model_refits=0,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
