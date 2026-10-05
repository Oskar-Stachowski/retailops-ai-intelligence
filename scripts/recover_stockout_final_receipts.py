"""Recover exact passed final receipts; no outcome evaluation or model fit occurs."""

import argparse
import json
import os
import re
import shutil
import stat
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from retailops_ai.source_snapshot.files import decode_json
from retailops_ai.stockout_campaign.contract import CampaignFreeze
from retailops_ai.stockout_campaign.download import MAX_METADATA, NoRedirect, _api, download


def api(path: str) -> Any:
    with urllib.request.build_opener(NoRedirect()).open(_api(path), timeout=30) as response:
        return decode_json(response.read(MAX_METADATA + 1), limit=MAX_METADATA)


def check_request(
    request: dict[str, Any], freeze: CampaignFreeze, run: dict[str, Any], jobs: list[dict[str, Any]]
) -> None:
    if (
        request["campaign_id"] != freeze.campaign_id
        or request["owner_authorized"] is not True
        or request["environment"] != "isolated_disposable_GitHub_runner"
        or request["production_deployment_authorized"] is not False
        or re.fullmatch(r"[0-9a-f]{40}", request["final_execution_commit"]) is None
        or run["id"] != request["final_run_id"]
        or run["head_sha"] != request["final_execution_commit"]
        or run["status"] != "completed"
        or run["conclusion"] not in {"success", "failure"}
        or len(set(request["successful_evaluation_job_ids"])) != 6
    ):
        raise ValueError("final_receipt_recovery_request_or_execution")
    matched = [j for j in jobs if j["id"] in request["successful_evaluation_job_ids"]]
    if len(matched) != 6 or any(
        j["conclusion"] != "success" or not j["name"].startswith("evaluate (") for j in matched
    ):
        raise ValueError("final_receipt_recovery_requires_six_successful_evaluations")
    expected = {
        f"ai08-final-{request['final_execution_commit']}-{s.world}-{s.seed}" for s in freeze.sources
    }
    expected.add("ai08-final-collected-" + request["final_execution_commit"])
    if {a["name"] for a in request["artifacts"]} != expected or len(request["artifacts"]) != 7:
        raise ValueError("final_receipt_recovery_exact_seven_artifacts_required")


def extract(archive: Path, destination: Path) -> None:
    """Bounded regular receipt files only; source.zip stays opaque until sealed replay."""
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 32 or sum(i.file_size for i in entries) > 128 * 1024**2:
            raise ValueError("final_receipt_recovery_archive_limits")
        paths = [Path(i.filename) for i in entries]
        if len(set(paths)) != len(paths) or any(
            p.is_absolute() or ".." in p.parts or len(p.parts) > 2 for p in paths
        ):
            raise ValueError("final_receipt_recovery_archive_paths")
        for entry, relative in zip(entries, paths, strict=True):
            if entry.is_dir():
                continue
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
                raise ValueError("final_receipt_recovery_regular_files_only")
            if relative.name not in {
                "native.json",
                "wheel.json",
                "resource.json",
                "source.zip",
                "native-access.jsonl",
                "wheel-access.jsonl",
                "final_quality.json",
                "execution_evidence.json",
            }:
                raise ValueError("final_receipt_recovery_unknown_receipt_file")
            target = destination / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with bundle.open(entry) as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024**2)
            target.chmod(0o600)


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
        raise ValueError("final_receipt_recovery_owned_runner_reserve_required")
    request = json.loads(args.request.read_bytes())
    freeze = CampaignFreeze.model_validate_json(args.freeze.read_bytes())
    run = api("actions/runs/" + str(request["final_run_id"]))
    jobs = api("actions/runs/" + str(request["final_run_id"]) + "/jobs?per_page=100")["jobs"]
    check_request(request, freeze, run, jobs)
    args.output.mkdir(mode=0o700)
    for artifact in request["artifacts"]:
        metadata = api("actions/artifacts/" + str(artifact["id"]))
        if (
            metadata["name"] != artifact["name"]
            or metadata["digest"] != "sha256:" + artifact["sha256"]
            or metadata["expired"] is not False
        ):
            raise ValueError("final_receipt_recovery_artifact_metadata_changed")
        # The existing downloader verifies the transport ID/run/bytes/SHA, without opening labels.
        transport = freeze.sources[0].model_copy(
            update=dict(
                workflow_run_id=request["final_run_id"],
                artifact_id=artifact["id"],
                artifact_bytes=artifact["bytes"],
                artifact_sha256=artifact["sha256"],
            )
        )
        archive = args.output / (str(artifact["id"]) + ".zip")
        download(transport, archive)
        extract(archive, args.output / artifact["name"])
        archive.unlink()
    print(
        json.dumps(
            dict(
                status="exact_final_receipts_recovered",
                final_run_id=request["final_run_id"],
                successful_evaluations=6,
                artifacts=7,
                evaluations_repeated=0,
                model_refits=0,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
