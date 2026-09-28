import json
import stat
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from test_chunks import config as config
from test_chunks import replace
from test_chunks import sources as sources
from test_index_lifecycle import approval
from test_indexes import embedding_config as embedding_config
from test_retrieval import candidate, fixture_golden, retrieval_config

from retailops_ai.cli import main
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.golden import GoldenReport
from retailops_ai.knowledge.indexes import IndexCandidate, vector_checksum
from retailops_ai.knowledge.qualification import (
    GoldenLabelsApproval,
    IndexReleaseManifest,
    SimilarityReview,
)
from retailops_ai.pipelines.golden import evaluate
from retailops_ai.pipelines.qualification import prepare_release, similarity_findings
from retailops_ai.pipelines.review import load_similarity_policy, review_similarity

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def inputs(sources, config, embedding_config):
    index = candidate(sources, config, embedding_config)
    golden = fixture_golden(index)
    retrieval = retrieval_config()
    return (
        index,
        golden,
        retrieval,
        evaluate(index, golden, retrieval),
        load_similarity_policy(ROOT / "knowledge/similarity.v1.json"),
    )


def labels_approval(inputs, **updates):
    index, golden, retrieval, _, _ = inputs
    value = {
        "schema_version": "1.0",
        "golden_set_id": golden.golden_set_id,
        "index_id": index.manifest.index_id,
        "retrieval_config_id": retrieval.config_id(),
        "environment": index.manifest.environment,
        "review_owner": golden.review_owner,
        "reviewer": golden.review_owner,
        "reviewer_kind": "human",
        "decision": "approved",
        "scope": "labels_tools_and_frozen_thresholds",
        "reviewed_at": "2026-09-28T14:30:00Z",
        **updates,
    }
    value["approval_id"] = "golden-approval-sha256-" + canonical_sha256(value)
    return GoldenLabelsApproval.model_validate_json(json.dumps(value))


def review(report, decisions=None, **updates):
    value = {
        "schema_version": "1.0",
        "report_id": report.report_id,
        "review_owner": report.review_owner,
        "reviewer": "fixture-technical-reviewer",
        "review_kind": "technical_lexical_review_only",
        "reviewed_at": "2026-09-28T14:30:00Z",
        "corpus_approval_created": False,
        "decisions": decisions
        if decisions is not None
        else [
            {
                "level": level,
                "kind": kind,
                "left_checksum": left,
                "right_checksum": right or None,
                "decision": "retain_separate_contexts",
                "reason": "Synthetic fixture retains distinct source and citation contexts.",
            }
            for level, kind, left, right in sorted(similarity_findings(report))
        ],
        **updates,
    }
    value["review_id"] = "similarity-review-sha256-" + canonical_sha256(value)
    return SimilarityReview.model_validate_json(json.dumps(value))


def test_same_inputs_give_same_manifest_and_perfect_fake_remains_blocked(inputs):
    result = prepare_release(*inputs)
    assert result == prepare_release(*inputs)
    assert result.index_manifest == inputs[0].manifest
    assert result.corpus_manifest == inputs[0].chunks.corpus
    assert result.golden_set == inputs[1]
    assert result.golden_report == inputs[3]
    assert result.mechanical_validation.result == "passed"
    assert result.golden_report.measured_thresholds_passed
    assert result.blockers == (
        "corpus_approval_missing",
        "golden_labels_approval_missing",
        "semantic_provider_required",
        "user_build_profile_required",
    )
    assert result.activation_allowed is False
    assert result.status == "blocked"
    assert result.corpus_approval is None and result.golden_approval is None


def test_explicit_decisions_remove_only_their_corresponding_blockers(inputs):
    corpus_approval = approval(inputs[0])
    golden_approval = labels_approval(inputs)
    result = prepare_release(
        *inputs, corpus_approval=corpus_approval, golden_approval=golden_approval
    )
    assert result.blockers == ("semantic_provider_required", "user_build_profile_required")
    assert not result.activation_allowed
    assert not result.golden_report.labels_approved  # Earlier measurement remains unchanged.
    for name, value in [
        ("golden-labels-approval", golden_approval),
        ("index-release-manifest", result),
    ]:
        schema = json.loads((ROOT / f"contracts/knowledge/v1/{name}.v1.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(value.model_dump(mode="json"))


@pytest.mark.parametrize(
    "updates",
    [
        {"golden_set_id": "golden-set-sha256-" + "a" * 64},
        {"index_id": "index-sha256-" + "a" * 64},
        {"retrieval_config_id": "retrieval-config-sha256-" + "a" * 64},
        {"environment": "local"},
        {"review_owner": "another-owner", "reviewer": "another-owner"},
    ],
)
def test_labels_approval_cannot_be_reused_for_another_snapshot(inputs, updates):
    with pytest.raises(ValueError, match="release_golden_approval_mismatch"):
        prepare_release(*inputs, golden_approval=labels_approval(inputs, **updates))


@pytest.mark.parametrize(
    "updates",
    [
        {"corpus_id": "corpus-sha256-" + "a" * 64},
        {"corpus_config_id": "corpus-config-sha256-" + "a" * 64},
        {"environment": "local"},
        {"review_owner": "another-owner"},
    ],
)
def test_corpus_approval_cannot_be_reused_for_another_snapshot(inputs, updates):
    with pytest.raises(ValueError, match="release_corpus_approval_mismatch"):
        prepare_release(*inputs, corpus_approval=approval(inputs[0], **updates))


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"reviewer": "someone-else"}, "golden_owner_review_required"),
        ({"reviewer_kind": "llm"}, "reviewer_kind"),
        ({"scope": "sources_only"}, "scope"),
        ({"reviewed_at": "2026-09-28T16:30:00+02:00"}, "utc_timestamp_required"),
    ],
)
def test_labels_decision_requires_explicit_owner_scope_and_utc(inputs, updates, code):
    with pytest.raises(ValidationError, match=code):
        labels_approval(inputs, **updates)


@pytest.mark.parametrize(
    "mutation",
    ["recall", "case_outcome", "chunk_id", "critical", "coverage", "order", "p95", "gate"],
)
def test_supplied_golden_report_is_reproduced_instead_of_trusting_summary(inputs, mutation):
    index, golden, retrieval, report, policy = inputs
    raw = report.model_dump(mode="json")
    if mutation == "recall":
        raw["recall_at_5"] = 0.5
    elif mutation == "case_outcome":
        raw["cases"][0]["outcome"] = "forbidden"
    elif mutation == "chunk_id":
        raw["cases"][0]["retrieved_chunk_ids"] = ["chunk-sha256-" + "a" * 64]
    elif mutation == "critical":
        raw["cases"][0]["critical"] = not raw["cases"][0]["critical"]
    elif mutation == "coverage":
        raw["cases"].pop()
    elif mutation == "order":
        raw["cases"].reverse()
    elif mutation == "p95":
        raw["latency_p95_ms"] += 1
    else:
        raw["measured_thresholds_passed"] = not raw["measured_thresholds_passed"]
    forged = GoldenReport.model_validate_json(json.dumps(raw))
    with pytest.raises(ValueError, match="release_golden_.*mismatch"):
        prepare_release(index, golden, retrieval, forged, policy)


def test_valid_but_wrong_fake_vectors_remain_a_mechanical_blocker(inputs):
    raw = deepcopy(inputs[0].model_dump(mode="json"))
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
    changed = IndexCandidate.model_validate_json(json.dumps(raw))
    golden = fixture_golden(changed)
    report = evaluate(changed, golden, inputs[2])
    result = prepare_release(changed, golden, inputs[2], report, inputs[4])
    assert "mechanical_validation_failed" in result.blockers
    assert result.mechanical_validation.result == "failed"
    assert not result.activation_allowed


def similar_inputs(sources, config, embedding_config):
    shared = " ".join(f"word{i}" for i in range(40))
    replace(sources, f"# Source A\n\n{shared}\n\n{shared}\n\n{shared} enabled\n", 0)
    replace(sources, f"# Source B\n\n{shared}\n\n{shared} forbidden\n", 1)
    index = candidate(sources, config, embedding_config)
    golden = fixture_golden(index)
    retrieval = retrieval_config()
    policy = load_similarity_policy(ROOT / "knowledge/similarity.v1.json")
    return index, golden, retrieval, evaluate(index, golden, retrieval), policy


def test_each_exact_near_and_repeated_finding_needs_a_bound_decision(
    sources, config, embedding_config
):
    inputs = similar_inputs(sources, config, embedding_config)
    similarity = review_similarity(inputs[0].chunks, inputs[4])
    decisions = review(similarity)
    assert {d.kind for d in decisions.decisions} == {"exact", "near", "repeated_occurrence"}
    assert "similarity_review_incomplete" in prepare_release(*inputs).blockers
    partial = review(similarity, decisions.model_dump(mode="json")["decisions"][:-1])
    pending = prepare_release(*inputs, similarity_review=partial)
    assert pending.unreviewed_similarity_findings == 1
    assert "similarity_review_incomplete" in pending.blockers
    completed = prepare_release(*inputs, similarity_review=decisions)
    assert completed.unreviewed_similarity_findings == 0
    assert "similarity_review_incomplete" not in completed.blockers
    assert "corpus_approval_missing" in completed.blockers
    assert not completed.activation_allowed
    schema = json.loads(
        (ROOT / "contracts/knowledge/v1/similarity-review.v1.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(decisions.model_dump(mode="json"))


@pytest.mark.parametrize("mutation", ["report", "owner", "extra", "duplicate"])
def test_stale_unknown_and_duplicate_similarity_decisions_fail(
    sources, config, embedding_config, mutation
):
    inputs = similar_inputs(sources, config, embedding_config)
    similarity = review_similarity(inputs[0].chunks, inputs[4])
    decisions = review(similarity).model_dump(mode="json")["decisions"]
    updates = {}
    if mutation == "report":
        updates["report_id"] = "similarity-report-sha256-" + "a" * 64
    elif mutation == "owner":
        updates["review_owner"] = "someone-else"
    elif mutation == "extra":
        decisions[0]["left_checksum"] = "0" * 64
    else:
        decisions.insert(0, decisions[0])
    with pytest.raises(ValueError, match="similarity"):
        prepare_release(*inputs, similarity_review=review(similarity, decisions, **updates))


@pytest.mark.parametrize(
    "field,value", [("activation_allowed", True), ("blockers", []), ("status", "ready")]
)
def test_rehashed_manifest_cannot_claim_activation_or_remove_blockers(inputs, field, value):
    raw = prepare_release(*inputs).model_dump(mode="json")
    raw[field] = value
    raw.pop("release_id")
    raw["release_id"] = "rag-release-manifest-sha256-" + canonical_sha256(raw)
    with pytest.raises(ValidationError):
        IndexReleaseManifest.model_validate_json(json.dumps(raw))


def test_cli_is_private_offline_and_never_overwrites_output(inputs, tmp_path, monkeypatch, capsys):
    paths = {}
    for name, value in zip(
        ("candidate", "golden-set", "retrieval-config", "golden-report", "similarity-policy"),
        inputs,
        strict=True,
    ):
        path = tmp_path / (name + ".json")
        path.write_text(value.model_dump_json())
        paths[name] = path
    output = tmp_path / "release.json"
    args = ["index-release-check"]
    for name, path in paths.items():
        args += ["--" + name, str(path)]
    args += ["--output", str(output)]
    monkeypatch.setenv("APP_ENV", "invalid-release-settings")
    monkeypatch.setattr(
        "retailops_ai.cli.load_settings", lambda *a, **k: pytest.fail("settings used")
    )
    assert main(args) == 0
    first = output.read_bytes()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert b'"text":' not in first and b'"vector":' not in first
    console = capsys.readouterr()
    assert json.loads(console.out)["status"] == "blocked"
    assert "Body 0" not in console.out and not console.err
    assert main(args) == 2
    assert output.read_bytes() == first
    assert capsys.readouterr().err.strip() == '{"error":"index_release_check_failed"}'
    bad = json.loads(paths["golden-report"].read_text())
    bad["cases"].pop()
    paths["golden-report"].write_text(json.dumps(bad))
    second = tmp_path / "failed.json"
    args[-1] = str(second)
    assert main(args) == 2
    assert not second.exists()
    assert not list(tmp_path.glob("tmp*"))
