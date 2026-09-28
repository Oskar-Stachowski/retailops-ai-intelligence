import json
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from test_chunks import build
from test_chunks import config as config
from test_chunks import sources as sources
from test_indexes import embedding_config as embedding_config

from retailops_ai.adapters.git_documents import CorpusError
from retailops_ai.adapters.index_lifecycle import qualify_index, switch_index
from retailops_ai.cli import main
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.indexes import IndexCandidate, vector_checksum
from retailops_ai.knowledge.releases import (
    CorpusApproval,
    IndexPin,
    IndexValidation,
    SwitchRequest,
    approval_matches,
)
from retailops_ai.pipelines.indexes import build_index
from retailops_ai.pipelines.releases import load_release_document, validate_candidate

ROOT = Path(__file__).resolve().parents[1]


def approval(candidate, **updates):
    corpus = candidate.chunks.corpus
    raw = {
        "schema_version": "1.0",
        "corpus_id": corpus.corpus_id,
        "corpus_config_id": corpus.corpus_config_id,
        "environment": corpus.environment,
        "review_owner": corpus.review_owner,
        "reviewer": "fixture-review-pipeline",
        "reviewer_kind": "approved_pipeline",
        "decision": "approved",
        "scope": "sources_status_access_and_exclusions",
        "reviewed_at": "2026-09-28T10:00:00Z",
        **updates,
    }
    raw["review_id"] = "corpus-review-sha256-" + canonical_sha256(raw)
    return CorpusApproval.model_validate_json(json.dumps(raw))


def request(**updates):
    return SwitchRequest(
        schema_version="1.0",
        request_id="rag-change-" + "a" * 32,
        environment="test",
        lane="offline_test",
        operation="activate",
        target_index_id="index-sha256-" + "a" * 64,
        expected_generation=0,
        actor="fixture-promoter",
        **updates,
    )


def test_validation_is_deterministic_and_does_not_approve_quality(
    sources, config, embedding_config
):
    candidate = build_index(build(sources, config), embedding_config)
    report = validate_candidate(candidate)
    assert report == validate_candidate(candidate)
    assert report.result == "passed"
    assert report.golden_evaluation == "not_evaluated_fake_vectors"
    reviewed = approval(candidate)
    corpus = candidate.chunks.corpus
    assert approval_matches(
        reviewed, corpus.corpus_id, corpus.corpus_config_id, corpus.review_owner, "test"
    )
    assert not approval_matches(
        reviewed, "corpus-sha256-" + "f" * 64, corpus.corpus_config_id, corpus.review_owner, "test"
    )
    assert not approval_matches(
        reviewed, corpus.corpus_id, corpus.corpus_config_id, "another-owner", "test"
    )
    assert not approval_matches(
        reviewed, corpus.corpus_id, corpus.corpus_config_id, corpus.review_owner, "local"
    )
    for name, model in [
        ("corpus-approval", reviewed),
        ("index-validation", report),
        ("switch-request", request()),
    ]:
        schema = json.loads((ROOT / f"contracts/knowledge/v1/{name}.v1.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(model.model_dump(mode="json"))


def test_mechanical_acceptance_rejects_valid_but_different_fake_vectors(
    sources, config, embedding_config
):
    raw = deepcopy(build_index(build(sources, config), embedding_config).model_dump(mode="json"))
    checksums = {}
    for record in raw["embeddings"]:
        record["vector"] = [-v for v in record["vector"]]
        record["vector_checksum"] = vector_checksum(tuple(record["vector"]))
        checksums[record["embedding_id"]] = record["vector_checksum"]
    for entry in raw["manifest"]["entries"]:
        entry["vector_checksum"] = checksums[entry["embedding_id"]]
    raw["manifest"]["index_id"] = "index-sha256-" + canonical_sha256(
        {k: v for k, v in raw["manifest"].items() if k != "index_id"}
    )
    candidate = IndexCandidate.model_validate_json(json.dumps(raw))
    report = validate_candidate(candidate)
    assert report.result == "failed"
    assert not report.checks.deterministic_fake_vectors


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "proposed"),
        ("scope", "sources_only"),
        ("reviewer_kind", "llm"),
        ("environment", "production"),
        ("review_id", "corpus-review-sha256-" + "0" * 64),
        ("reviewed_at", "2026-09-28T10:00:00+02:00"),
    ],
)
def test_corpus_approval_rejects_invalid_or_unbound_decisions(
    sources, config, embedding_config, field, value
):
    candidate = build_index(build(sources, config), embedding_config)
    raw = approval(candidate).model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValidationError):
        CorpusApproval.model_validate_json(json.dumps(raw))


def test_human_decision_requires_named_owner(sources, config, embedding_config):
    candidate = build_index(build(sources, config), embedding_config)
    with pytest.raises(ValidationError, match="owner_review"):
        approval(candidate, reviewer_kind="human", reviewer="someone-else")
    assert approval(candidate, reviewer_kind="human", reviewer=candidate.chunks.corpus.review_owner)


@pytest.mark.parametrize("tamper", ["passed_failed_checks", "identity", "semantic_quality"])
def test_validation_report_is_closed_and_does_not_accept_fake_quality_claims(
    sources, config, embedding_config, tamper
):
    raw = validate_candidate(build_index(build(sources, config), embedding_config)).model_dump(
        mode="json"
    )
    if tamper == "passed_failed_checks":
        raw["checks"]["source_metadata"] = False
    elif tamper == "identity":
        raw["index_id"] = "index-sha256-" + "0" * 64
    else:
        raw["golden_evaluation"] = "passed"
    if tamper != "identity":
        raw["validation_id"] = "index-validation-sha256-" + canonical_sha256(
            {k: v for k, v in raw.items() if k != "validation_id"}
        )
    with pytest.raises(ValidationError):
        IndexValidation.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    "environment,lane,code",
    [
        ("local", "offline_test", "requires_test"),
    ],
)
def test_fake_switch_cannot_bypass_test_environment_or_golden_gate(environment, lane, code):
    raw = request().model_dump(mode="json")
    raw.update(environment=environment, lane=lane)
    with pytest.raises(CorpusError, match=code):
        switch_index(None, SwitchRequest.model_validate(raw))


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "arbitrary"),
        ("target_index_id", "other"),
        ("expected_generation", -1),
        ("expected_generation", True),
        ("expected_generation", 2**63),
        ("actor", "private marker\n"),
        ("lane", "production"),
        ("operation", "delete"),
    ],
)
def test_switch_request_is_bounded_strict_and_typed(field, value):
    raw = request().model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValidationError):
        SwitchRequest.model_validate_json(json.dumps(raw))


def test_qualification_gate_is_checked_before_database(sources, config, embedding_config):
    candidate = build_index(build(sources, config), embedding_config)
    with pytest.raises(CorpusError, match="golden_evaluation"):
        qualify_index(
            None,
            candidate.manifest.index_id,
            "test",
            "retrieval",
            approval(candidate),
            validate_candidate(candidate),
        )
    with pytest.raises(CorpusError, match="requires_test"):
        qualify_index(
            None,
            candidate.manifest.index_id,
            "local",
            "offline_test",
            approval(candidate),
            validate_candidate(candidate),
        )


def test_validation_cli_offline_atomic_output_and_redacted_errors(
    sources, config, embedding_config, tmp_path, monkeypatch, capsys
):
    path = tmp_path / "candidate.json"
    candidate = build_index(build(sources, config), embedding_config)
    path.write_text(candidate.model_dump_json())
    report = tmp_path / "validation.json"
    monkeypatch.setenv("APP_ENV", "invalid-private-marker")
    argv = ["index-validate", "--candidate", str(path), "--output", str(report)]
    assert main(argv) == 0
    original = report.read_bytes()
    assert load_release_document(report, IndexValidation) == validate_candidate(candidate)
    assert report.stat().st_mode & 0o777 == 0o600
    assert main(argv) == 2
    assert report.read_bytes() == original
    path.write_text('{"private-marker":NaN}')
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert "private-marker" not in captured.out + captured.err


def test_pin_does_not_allow_local_environment_or_wrong_manifest(sources, config, embedding_config):
    candidate = build_index(build(sources, config), embedding_config)
    raw = {
        "schema_version": "1.0",
        "environment": "test",
        "lane": "offline_test",
        "purpose": "lifecycle_validation_only",
        "generation": 1,
        "request_id": request().request_id,
        "review_id": approval(candidate).review_id,
        "validation_id": validate_candidate(candidate).validation_id,
        "manifest": candidate.manifest.model_dump(mode="json"),
    }
    pin = IndexPin.model_validate_json(json.dumps(raw))
    assert pin.manifest == candidate.manifest
    raw["environment"] = "local"
    with pytest.raises(ValidationError):
        IndexPin.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    "raw", [b'{"decision":"approved","decision":"approved"}', b'{"x":NaN}', b"\xff", b" " * 500001]
)
def test_release_artifact_loader_rejects_ambiguous_nonfinite_and_large_inputs(tmp_path, raw):
    path = tmp_path / "approval.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        load_release_document(path, CorpusApproval)
