"""The owner exception cannot promote a different run or erase failed measurements."""

import json
from copy import deepcopy

import pytest

from scripts.check_ai04_acceptance import DECISION, EVIDENCE, verify_acceptance


def decision():
    return json.loads(DECISION.read_bytes())


def test_final_v12_stage_acceptance_preserves_failed_quality_and_deployment_boundary():
    result = verify_acceptance(decision(), EVIDENCE)
    assert result["stage_status_after_protected_publication"] == "ready"
    assert result["original_quality_status"] == "not_ready"
    assert result["accepted_deviations"] == 3
    assert result["production_deployment_authorized"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "functional-v12-run-sha256-" + "0" * 64),
        ("selected_version", "v13"),
        ("production_deployment_authorized", True),
        ("registry_promotion_authorized", True),
        ("original_quality_status", "ready"),
        ("statistical_insignificance_established", True),
        ("authorization_text", ""),
    ],
)
def test_exception_cannot_expand_to_other_artifacts_or_claims(field, value):
    document = decision()
    document[field] = value
    with pytest.raises(ValueError):
        verify_acceptance(document, EVIDENCE)


@pytest.mark.parametrize("mutation", ["missing", "extra", "changed_value"])
def test_every_exception_must_match_the_original_measured_failure(mutation):
    document = decision()
    if mutation == "missing":
        document["accepted_deviations"].pop()
    elif mutation == "extra":
        document["accepted_deviations"].append(deepcopy(document["accepted_deviations"][0]))
    else:
        document["accepted_deviations"][0]["candidate_mse"] = 0
    with pytest.raises(ValueError, match="accepted_deviations_do_not_match"):
        verify_acceptance(document, EVIDENCE)


def test_tampered_metrics_cannot_be_relabelled_passed_even_with_an_updated_checksum(tmp_path):
    import hashlib

    document = decision()
    for name in document["evidence"]:
        (tmp_path / name).write_bytes((EVIDENCE / name).read_bytes())
    path = tmp_path / "metrics.json"
    metrics = json.loads(path.read_bytes())
    metrics["status"] = "ready"
    path.write_text(json.dumps(metrics))
    raw = path.read_bytes()
    document["evidence"]["metrics.json"] = {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }
    with pytest.raises(ValueError, match="original_metrics_changed"):
        verify_acceptance(document, tmp_path)
