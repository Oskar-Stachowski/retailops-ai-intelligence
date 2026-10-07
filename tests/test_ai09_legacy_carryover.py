"""Missing scratch state never becomes a new budget or an untouched holdout."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from retailops_ai.evaluation_campaign.legacy_carryover import (
    EVIDENCE_PINS,
    LegacyCampaignCarryover,
    load_legacy_carryover,
)
from retailops_ai.source_snapshot.files import SnapshotError

EVIDENCE = Path(__file__).resolve().parents[1] / "docs/evidence"


def test_published_history_is_preserved_without_opening_missing_historical_paths():
    result = load_legacy_carryover(EVIDENCE)
    assert result.published_fit_starts == 44
    assert result.published_fit_completions == 40
    assert len(result.published_attempts) == 11
    assert len({a.protocol_sha256 for a in result.published_attempts}) == 10
    assert result.legacy_budget_available == 0
    assert result.unavailable_legacy_read_slots_retired == 64
    assert result.unavailable_legacy_fit_slots_retired == 4
    assert not result.original_journal_bytes_recovered
    assert not result.original_artifact_bytes_recovered
    assert result.full_historical_cost == "unknown_not_zero"
    assert result.content_sha256() == load_legacy_carryover(EVIDENCE).content_sha256()


def test_unknown_source_identity_never_becomes_unseen():
    result = load_legacy_carryover(EVIDENCE)
    assert result.source_freshness(result.published_protocol.parent.source_dataset_id) == (
        "previously_exposed"
    )
    assert result.source_freshness("source-sha256-" + "f" * 64) == "unknown_not_unseen"
    assert not result.final_test_access_authorized
    assert not result.independent_evaluation_access_authorized


@pytest.mark.parametrize("name", EVIDENCE_PINS)
def test_resealed_or_missing_published_evidence_is_rejected_before_recovery(tmp_path, name):
    for filename in EVIDENCE_PINS:
        (tmp_path / filename).write_bytes((EVIDENCE / filename).read_bytes())
    original = (tmp_path / name).read_bytes()
    (tmp_path / name).write_bytes(original + b" ")
    with pytest.raises(SnapshotError, match="legacy_carryover_evidence_bytes_mismatch"):
        load_legacy_carryover(tmp_path)
    (tmp_path / name).unlink()
    with pytest.raises(SnapshotError):
        load_legacy_carryover(tmp_path)
    assert all(
        hashlib.sha256((EVIDENCE / filename).read_bytes()).hexdigest() == expected
        for filename, expected in EVIDENCE_PINS.items()
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("original_journal_bytes_recovered", True),
        ("original_artifact_bytes_recovered", True),
        ("pre_read_or_pre_fit_history_restored", True),
        ("legacy_budget_available", 1),
        ("unavailable_legacy_read_slots_retired", 0),
        ("unavailable_legacy_fit_slots_retired", 0),
        ("unlisted_data_freshness", "unseen"),
        ("final_test_access_authorized", True),
        ("independent_evaluation_access_authorized", True),
    ],
)
def test_metadata_cannot_reopen_lost_budgets_or_qualify_freshness(field, value):
    data = load_legacy_carryover(EVIDENCE).model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValidationError):
        LegacyCampaignCarryover.model_validate_json(json.dumps(data))


def test_dropping_an_interrupted_attempt_or_rewriting_its_fits_is_rejected():
    data = load_legacy_carryover(EVIDENCE).model_dump(mode="json")
    data["published_attempts"][0]["model_starts"] = 3
    with pytest.raises(ValidationError, match="legacy_published_metadata_digest_mismatch"):
        LegacyCampaignCarryover.model_validate_json(json.dumps(data))


def test_nested_mutation_cannot_be_bound_as_valid_carryover():
    result = load_legacy_carryover(EVIDENCE)
    result.evidence_sha256["09-05-development-trial-registry.json"] = "f" * 64
    with pytest.raises(ValidationError, match="legacy_evidence_pins_mismatch"):
        result.content_sha256()


def test_known_protocol_cannot_be_relabelled_as_a_different_seed():
    result = load_legacy_carryover(EVIDENCE)
    result.published_protocol.source_parameters["seed"] = 137
    with pytest.raises(ValidationError, match="legacy_published_metadata_digest_mismatch"):
        result.source_freshness("source-sha256-" + "f" * 64)
