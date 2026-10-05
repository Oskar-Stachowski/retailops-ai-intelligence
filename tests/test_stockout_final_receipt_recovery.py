"""Qualification recovery requires all six successful jobs and exact sealed artifacts."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

from retailops_ai.stockout_campaign.contract import CampaignFreeze

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "stockout_final_receipt_recovery", ROOT / "scripts/recover_stockout_final_receipts.py"
)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def evidence():
    request = json.loads(
        (ROOT / "docs/reference/stockout-final-acceptance-request-v1.json").read_bytes()
    )
    freeze = CampaignFreeze.model_validate_json(
        (ROOT / "docs/reference/stockout-final-campaign-v2.json").read_bytes()
    )
    run = dict(
        id=request["final_run_id"],
        head_sha=request["final_execution_commit"],
        status="completed",
        conclusion="failure",
    )
    jobs = [
        dict(id=id, name="evaluate (world, seed)", conclusion="success")
        for id in request["successful_evaluation_job_ids"]
    ]
    return request, freeze, run, jobs


def test_failed_qualification_does_not_discard_six_successful_final_evaluations():
    subject.check_request(*evidence())


@pytest.mark.parametrize(
    "mutation",
    ["evaluation_failed", "wrong_commit", "missing_artifact", "foreign_job", "production"],
)
def test_failed_missing_or_foreign_final_evidence_cannot_be_recovered(mutation):
    request, freeze, run, jobs = deepcopy(evidence())
    if mutation == "evaluation_failed":
        jobs[0]["conclusion"] = "failure"
    elif mutation == "wrong_commit":
        run["head_sha"] = "a" * 40
    elif mutation == "missing_artifact":
        request["artifacts"].pop()
    elif mutation == "foreign_job":
        jobs[0]["name"] = "unrelated successful job"
    else:
        request["production_deployment_authorized"] = True
    with pytest.raises(ValueError):
        subject.check_request(request, freeze, run, jobs)
