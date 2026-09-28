import hashlib
import itertools
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from test_chunks import build, commit, replace
from test_chunks import config as config
from test_chunks import sources as sources

from retailops_ai.adapters.git_documents import CorpusError
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.contracts import REPOSITORIES
from retailops_ai.knowledge.review import SimilarityPolicy, SimilarityReport
from retailops_ai.pipelines.review import load_similarity_policy, review_similarity

ROOT = Path(__file__).resolve().parents[1]
LONG = " ".join(f"word{i}" for i in range(40))


@pytest.fixture
def policy():
    return load_similarity_policy(ROOT / "knowledge/similarity.v1.json")


def related(sources):
    replace(sources, f"# Source A\n\n{LONG} enabled\n\n{LONG} disabled\n", 0)
    replace(sources, f"# Source B\n\n{LONG} not enabled\n\n{LONG} forbidden\n", 1)


def test_exact_groups_preserve_access_status_scope_citations_and_every_chunk(
    sources, config, policy
):
    for i in range(2):
        replace(sources, f"# Source {i}\n\n{LONG}\n", i)
    registration = sources[0]["sources"][1]["documents"][0]
    registration.update(
        document_status="historical", access_class="restricted", fact_scope="Historical only."
    )
    manifest = build(sources, config)
    before = manifest.model_dump_json()
    report = review_similarity(manifest, policy)
    assert manifest.model_dump_json() == before
    assert len(report.chunks.members) == len(manifest.chunks) == 2
    for analysis in (report.documents, report.chunks):
        assert len(analysis.content_groups) == 1
        group = analysis.content_groups[0]
        assert len(group.member_ids) == 2
        assert {"access_class", "document_status", "repository", "fact_scope"} <= set(
            group.metadata_differences
        )
        assert not analysis.near_pairs
    assert report.outcome == "review_required" and not report.activation_allowed
    for member in report.chunks.members:
        chunk = next(c for c in manifest.chunks if c.chunk_id == member.unit_id)
        assert member.source_refs == tuple(o.source_ref for o in chunk.occurrences)
    assert "text" not in report.chunks.members[0].model_dump()


def test_negation_number_and_code_changes_are_candidates_never_merged(sources, config, policy):
    replace(sources, f"# Source\n\n{LONG} enabled limit 100\n", 0)
    replace(sources, f"# Source\n\n```text\n{LONG} not enabled limit 101\n```\n", 1)
    manifest = build(sources, config)
    report = review_similarity(manifest, policy)
    assert len(report.chunks.near_pairs) == len(report.documents.near_pairs) == 1
    assert 8000 <= report.chunks.near_pairs[0].similarity_bps < 10000
    assert "block_type" in report.chunks.near_pairs[0].metadata_differences
    assert len(manifest.chunks) == len(report.chunks.members) == 2
    assert any("not enabled limit 101" in c.text for c in manifest.chunks)


def test_sparse_comparison_matches_independent_all_pairs_oracle(sources, config, policy):
    related(sources)
    replace(
        sources,
        f"# Source\n\n{LONG} not enabled\n\n{LONG} forbidden\n\ncompletely unrelated small words here\n",
        1,
    )
    manifest = build(sources, config)
    report = review_similarity(manifest, policy)
    bodies = {
        d.document_id: "\n".join(c.text for c in manifest.chunks if c.document_id == d.document_id)
        for d in manifest.corpus.documents
    }
    for analysis, texts in (
        (report.documents, bodies),
        (report.chunks, {c.chunk_id: c.text for c in manifest.chunks}),
    ):
        unique = {hashlib.sha256(text.encode()).hexdigest(): text for text in texts.values()}
        shingles = {}
        for checksum, text in unique.items():
            words = text.lower().split()
            shingles[checksum] = (
                set(zip(words, words[1:], words[2:], strict=False)) if len(words) >= 12 else set()
            )
        expected = {}
        for left, right in itertools.combinations(sorted(shingles), 2):
            a, b = shingles[left], shingles[right]
            intersection, union = len(a & b), len(a | b)
            if a and b and intersection * 10000 >= 8000 * union:
                expected[left, right] = intersection * 10000 // union
        assert {
            (p.left_checksum, p.right_checksum): p.similarity_bps for p in analysis.near_pairs
        } == expected


def test_short_texts_are_explicitly_unscored_but_exact_short_content_is_reported(
    sources, config, policy
):
    replace(sources, "# Short\n\nSmall body\n", 0)
    replace(sources, "# Short\n\nSmall body\n\nSmall change\n", 1)
    report = review_similarity(build(sources, config), policy)
    assert not report.documents.near_pairs and not report.chunks.near_pairs
    assert all(
        not g.eligible_for_near
        for g in (*report.documents.content_groups, *report.chunks.content_groups)
    )
    assert any(len(g.member_ids) == 2 for g in report.chunks.content_groups)
    assert report.outcome == "review_required"


def test_navigation_only_documents_are_covered_without_false_empty_duplicates(
    sources, config, policy
):
    for i in range(2):
        replace(sources, "# Title\n\n## Navigation\n\n[Link](#title)\n", i)
    report = review_similarity(build(sources, config), policy)
    assert len(report.documents.members) == 2 and not report.chunks.members
    assert report.documents.content_groups[0].utf8_bytes == 0
    assert report.outcome == "no_lexical_candidates" and not report.activation_allowed


def test_repeated_occurrences_remain_cited_after_existing_chunk_deduplication(
    sources, config, policy
):
    replace(sources, "# Repeated\n\nSame short body\n\nSame short body\n", 0)
    report = review_similarity(build(sources, config), policy)
    assert report.repeated_chunk_occurrences == 1
    assert any(len(m.source_refs) == 2 for m in report.chunks.members)
    assert report.outcome == "review_required"


def test_unicode_case_and_compatibility_normalization_is_only_a_near_hint(sources, config, policy):
    replace(
        sources,
        "# Unicode\n\n"
        + " ".join(["ŻÓŁĆ", "ＣＯＤＥ", "Straße", *[f"w{i}" for i in range(20)]])
        + "\n",
        0,
    )
    replace(
        sources,
        "# Unicode\n\n"
        + " ".join(["żółć", "code", "strasse", *[f"w{i}" for i in range(20)]])
        + "\n",
        1,
    )
    report = review_similarity(build(sources, config), policy)
    assert len(report.chunks.content_groups) == 2
    assert report.chunks.near_pairs[0].similarity_bps == 10000


@pytest.mark.parametrize(
    "field,code",
    [
        ("max_features", "similarity_feature_budget_exceeded"),
        ("max_candidate_pairs", "similarity_pair_budget_exceeded"),
        ("max_posting_visits", "similarity_posting_budget_exceeded"),
        ("max_report_pairs", "similarity_report_budget_exceeded"),
    ],
)
def test_every_budget_aborts_complete_review_without_partial_result(
    sources, config, policy, field, code
):
    related(sources)
    value = policy.model_dump(mode="json")
    value[field] = 1
    limited = SimilarityPolicy.model_validate_json(json.dumps(value))
    with pytest.raises(CorpusError, match=code):
        review_similarity(build(sources, config), limited)


def test_policy_threshold_changes_report_identity_without_changing_manifest(
    sources, config, policy
):
    related(sources)
    manifest = build(sources, config)
    baseline = review_similarity(manifest, policy)
    value = policy.model_dump(mode="json")
    value["threshold_bps"] = 10000
    strict = review_similarity(manifest, SimilarityPolicy.model_validate_json(json.dumps(value)))
    assert not strict.chunks.near_pairs
    assert baseline.report_id != strict.report_id
    assert baseline.chunk_manifest_id == strict.chunk_manifest_id == manifest.chunk_manifest_id


def test_revision_change_updates_citations_but_keeps_unchanged_content_groups_and_chunk_ids(
    sources, config, policy
):
    related(sources)
    before = review_similarity(build(sources, config), policy)
    source = sources[0]["sources"][0]
    repo = sources[1][source["repository"]]
    (repo / "docs/unreviewed.md").write_text("Changed excluded source, not corpus text.")
    source["commit_sha"] = commit(repo)
    after = review_similarity(build(sources, config), policy)
    assert before.report_id != after.report_id
    assert before.chunk_manifest_id != after.chunk_manifest_id
    assert before.chunks.content_groups == after.chunks.content_groups
    assert [m.unit_id for m in before.chunks.members] == [m.unit_id for m in after.chunks.members]
    assert [m.source_refs for m in before.chunks.members] != [
        m.source_refs for m in after.chunks.members
    ]


def test_registration_order_is_irrelevant_to_logical_report(sources, config, policy):
    related(sources)
    before = review_similarity(build(sources, config), policy)
    sources[0]["sources"].reverse()
    after = review_similarity(build(sources, config), policy)
    assert before.model_dump_json() == after.model_dump_json()


def test_unicode_runtime_drift_is_rejected(sources, config, policy, monkeypatch):
    monkeypatch.setattr("retailops_ai.pipelines.review.unicodedata.unidata_version", "0.0.0")
    with pytest.raises(CorpusError, match="similarity_unicode_version_mismatch"):
        review_similarity(build(sources, config), policy)


@pytest.mark.parametrize(
    "mutation",
    [
        "coverage",
        "citation",
        "metadata",
        "score",
        "pair_order",
        "eligibility",
        "budget",
        "outcome",
        "activation",
        "identity",
    ],
)
def test_contract_rejects_inconsistent_graph_even_with_recomputed_report_hash(
    sources, config, policy, mutation
):
    related(sources)
    value = review_similarity(build(sources, config), policy).model_dump(mode="json")
    if mutation == "coverage":
        value["chunks"]["content_groups"].pop()
    elif mutation == "citation":
        value["chunks"]["members"][0]["source_refs"][0] += "?bad=1"
    elif mutation == "metadata":
        value["chunks"]["members"][0]["document"]["access_class"] = "restricted"
    elif mutation == "score":
        value["chunks"]["near_pairs"][0]["similarity_bps"] = 1
    elif mutation == "pair_order":
        value["chunks"]["near_pairs"].append(deepcopy(value["chunks"]["near_pairs"][0]))
    elif mutation == "eligibility":
        value["chunks"]["content_groups"][0]["eligible_for_near"] = False
    elif mutation == "budget":
        value["chunks"]["posting_visits"] = policy.max_posting_visits + 1
    elif mutation == "outcome":
        value["outcome"] = "no_lexical_candidates"
    elif mutation == "activation":
        value["activation_allowed"] = True
    value["report_id"] = "similarity-report-sha256-" + canonical_sha256(
        {k: v for k, v in value.items() if k != "report_id"}
    )
    if mutation == "identity":
        value["chunk_manifest_id"] = "chunks-sha256-" + "f" * 64
    with pytest.raises(ValidationError):
        SimilarityReport.model_validate_json(json.dumps(value))


def cli_args(sources, tmp_path, output, policy_path=None):
    payload, repos = sources
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(payload))
    return [
        sys.executable,
        "-m",
        "retailops_ai",
        "corpus-review",
        "--registry",
        str(registry),
        "--chunker-config",
        str(ROOT / "knowledge/chunker.v1.json"),
        "--similarity-policy",
        str(policy_path or ROOT / "knowledge/similarity.v1.json"),
        "--retailops-repo",
        str(repos[REPOSITORIES[0]]),
        "--ai-repo",
        str(repos[REPOSITORIES[1]]),
        "--output",
        str(output),
    ]


def test_cli_rebuilds_pinned_git_ignores_worktree_is_reproducible_and_writes_private_new_file(
    sources, config, policy, tmp_path
):
    related(sources)
    expected = review_similarity(build(sources, config), policy)
    for repo in sources[1].values():
        (repo / "docs/guide.md").write_text("UNCOMMITTED-private-body-marker")
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    for seed, path in (("1", first), ("991", second)):
        result = subprocess.run(
            cli_args(sources, tmp_path, path),
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed, "APP_ENV": "private-config-marker"},
        )
        assert result.returncode == 0
        summary = json.loads(result.stdout)
        assert summary["report_id"] == expected.report_id and not summary["activation_allowed"]
        assert "word0" not in result.stdout and "private" not in result.stderr
        assert path.stat().st_mode & 0o777 == 0o600
    assert first.read_bytes() == second.read_bytes()
    assert "private-body-marker" not in first.read_text()
    before = first.read_bytes()
    replay = subprocess.run(cli_args(sources, tmp_path, first), capture_output=True, text=True)
    assert replay.returncode == 2 and first.read_bytes() == before
    assert str(tmp_path) not in replay.stderr and "Traceback" not in replay.stderr


@pytest.mark.parametrize(
    "failure", ["invalid_policy", "duplicate_json_key", "budget", "missing_source"]
)
def test_cli_failure_is_safe_and_does_not_publish_partial_output(
    sources, tmp_path, policy, failure
):
    related(sources)
    value = policy.model_dump(mode="json")
    if failure == "invalid_policy":
        value["model"] = "secret-input-marker"
    elif failure == "budget":
        value["max_report_pairs"] = 1
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    if failure == "duplicate_json_key":
        path.write_text('{"schema_version":"1.0","schema_version":"secret-input-marker"}')
    if failure == "missing_source":
        sources[0]["sources"][0]["commit_sha"] = "f" * 40
    output = tmp_path / "report.json"
    result = subprocess.run(
        cli_args(sources, tmp_path, output, path), capture_output=True, text=True
    )
    assert result.returncode == 2 and not output.exists()
    assert str(tmp_path) not in result.stderr and "secret-input-marker" not in result.stderr
    assert not result.stdout and "Traceback" not in result.stderr


def test_json_schema_snapshots_validate_policy_and_report(sources, config, policy):
    for name, model, value in [
        ("similarity-policy", SimilarityPolicy, policy),
        ("similarity-report", SimilarityReport, review_similarity(build(sources, config), policy)),
    ]:
        schema = json.loads((ROOT / f"contracts/knowledge/v1/{name}.v1.schema.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(value.model_dump(mode="json"), schema)
        assert schema["properties"] == model.model_json_schema()["properties"]
