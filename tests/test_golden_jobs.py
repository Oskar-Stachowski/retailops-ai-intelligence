import importlib.util
import json
import stat
from pathlib import Path

import jsonschema
import pytest
from alembic import op
from pydantic import ValidationError
from sqlalchemy import text
from test_chunks import config as config
from test_chunks import replace
from test_chunks import sources as sources
from test_index_lifecycle import approval
from test_indexes import embedding_config as embedding_config
from test_qualification import inputs as inputs
from test_qualification import labels_approval, review, similar_inputs
from test_retrieval import candidate, fixture_golden, retrieval_config

from retailops_ai import cli_index_jobs
from retailops_ai.cli import main
from retailops_ai.cli_index_jobs import load_profile
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.golden import GoldenSet
from retailops_ai.knowledge.jobs import GoldenIndexBuildProfile, GoldenIndexRunReport
from retailops_ai.pipelines.golden import evaluate
from retailops_ai.pipelines.index_builds import (
    build_run_report,
    check_build_profile,
    prepare_build_profile,
)
from retailops_ai.pipelines.releases import validate_candidate
from retailops_ai.pipelines.review import load_similarity_policy, review_similarity

ROOT = Path(__file__).resolve().parents[1]


def profile_for(inputs):
    index, golden, retrieval, _, policy = inputs
    return prepare_build_profile(
        index,
        golden,
        approval(index),
        labels_approval(inputs),
        retrieval,
        policy,
        review(review_similarity(index.chunks, policy)),
    )


@pytest.fixture
def profile(inputs):
    return profile_for(inputs)


def test_approved_profile_binds_golden_and_legacy_loader(profile, inputs, tmp_path):
    check_build_profile(profile)
    assert profile.request().evaluation_set_id == inputs[1].golden_set_id
    assert profile.request().corpus_config_id == inputs[0].chunks.corpus.corpus_config_id
    p = tmp_path / "profile.json"
    p.write_text(profile.model_dump_json())
    assert load_profile(p) == profile
    report = build_run_report(profile, inputs[0], validate_candidate(inputs[0]))
    assert isinstance(report, GoldenIndexRunReport)
    assert report.quality_gate_passed and not report.activation_allowed
    assert report.approval == profile.approval and report.golden_approval == profile.golden_approval
    for name, value in [
        ("golden-index-build-profile", profile),
        ("golden-index-run-report", report),
    ]:
        schema = json.loads((ROOT / f"contracts/knowledge/v1/{name}.v1.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(value.model_dump(mode="json"))


@pytest.mark.parametrize(
    "field",
    [
        "evaluation_set_id",
        "labels",
        "source_approval",
        "environment",
        "retrieval",
        "identity",
        "owner",
    ],
)
def test_profile_rejects_missing_or_mismatched_approved_inputs(profile, field):
    raw = profile.model_dump(mode="json")
    if field == "evaluation_set_id":
        raw[field] = "golden-set-sha256-" + "a" * 64
    elif field == "labels":
        raw["golden_approval"] = None
    elif field == "source_approval":
        raw["approval"] = None
    elif field == "environment":
        raw["environment"] = "local"
    elif field == "retrieval":
        raw["retrieval_config"]["max_per_document"] = 1
    elif field == "owner":
        raw["golden_approval"].update(
            review_owner="other-owner",
            reviewer="other-owner",
        )
        raw["golden_approval"].pop("approval_id")
        raw["golden_approval"]["approval_id"] = "golden-approval-sha256-" + canonical_sha256(
            raw["golden_approval"]
        )
    else:
        raw["profile_id"] = "index-build-profile-sha256-" + "a" * 64
    if field != "identity":
        raw["profile_id"] = "index-build-profile-sha256-" + canonical_sha256(
            {k: v for k, v in raw.items() if k != "profile_id"}
        )
    with pytest.raises(ValidationError):
        GoldenIndexBuildProfile.model_validate_json(json.dumps(raw))


def test_registered_profile_requires_all_lexical_decisions(sources, config, embedding_config):
    values = similar_inputs(sources, config, embedding_config)
    index, golden, retrieval, _, policy = values
    report = review_similarity(index.chunks, policy)
    full = review(report).model_dump(mode="json")["decisions"]
    with pytest.raises(ValueError, match="similarity_review_incomplete"):
        prepare_build_profile(
            index,
            golden,
            approval(index),
            labels_approval(values),
            retrieval,
            policy,
            review(report, full[:-1]),
        )
    with pytest.raises(ValueError, match="similarity_snapshot_mismatch"):
        prepare_build_profile(
            index,
            golden,
            approval(index),
            labels_approval(values),
            retrieval,
            policy,
            review(report, report_id="similarity-report-sha256-" + "a" * 64),
        )


def test_failing_retrieval_keeps_frozen_thresholds_and_produces_gate_failure(
    sources, config, embedding_config
):
    replace(
        sources,
        "# Document 0\n\n## Scope\n\n" + "\n\n".join(f"Body number {i}." for i in range(9)) + "\n",
    )
    index = candidate(sources, config, embedding_config)
    golden = fixture_golden(index)
    raw = golden.model_dump(mode="json")
    raw["cases"][0]["request"]["question"] = "some unrelated query"
    raw["cases"][0]["expected_sections"] = [
        {
            "repository": index.chunks.chunks[0].repository,
            "path": index.chunks.chunks[0].path,
            "heading_path": [h.title for h in index.chunks.chunks[0].heading_path],
            "document_status": "specified",
        }
    ]
    # Make a critical case explicitly expect refusal although the granted search is ok.
    next(c for c in raw["cases"] if c["critical"])["acceptable_outcomes"] = ["forbidden"]
    raw.pop("golden_set_id")
    raw["golden_set_id"] = "golden-set-sha256-" + canonical_sha256(raw)
    golden = GoldenSet.model_validate_json(json.dumps(raw))
    retrieval = retrieval_config()
    policy = load_similarity_policy(ROOT / "knowledge/similarity.v1.json")
    values = index, golden, retrieval, evaluate(index, golden, retrieval), policy
    profile = profile_for(values)
    result = build_run_report(profile, index, validate_candidate(index))
    assert not result.quality_gate_passed and not result.activation_allowed
    assert not result.golden.measured_thresholds_passed
    assert result.golden.thresholds == golden.thresholds


@pytest.mark.parametrize(
    "field", ["quality_gate_passed", "approval", "golden_approval", "activation_allowed"]
)
def test_run_report_cannot_rehash_a_gate_or_binding_away(profile, inputs, field):
    raw = build_run_report(profile, inputs[0], validate_candidate(inputs[0])).model_dump(
        mode="json"
    )
    if field == "quality_gate_passed":
        raw[field] = False
    elif field == "activation_allowed":
        raw[field] = True
    else:
        raw[field]["environment"] = "local"
    raw.pop("report_id")
    raw["report_id"] = "index-run-report-sha256-" + canonical_sha256(raw)
    with pytest.raises(ValidationError):
        GoldenIndexRunReport.model_validate_json(json.dumps(raw))


def test_preparation_and_report_export_are_private_exclusive_and_sanitized(
    inputs, profile, tmp_path, monkeypatch, capsys
):
    values = {
        "candidate": inputs[0],
        "golden-set": inputs[1],
        "corpus-approval": profile.approval,
        "golden-approval": profile.golden_approval,
        "retrieval-config": inputs[2],
        "similarity-policy": inputs[4],
        "similarity-review": profile.similarity_review,
    }
    args = ["knowledge-profile-prepare"]
    for name, value in values.items():
        path = tmp_path / (name + ".json")
        path.write_text(value.model_dump_json())
        args += ["--" + name, str(path)]
    output = tmp_path / "profile.json"
    args += ["--output", str(output)]
    monkeypatch.setenv("APP_ENV", "invalid-private-settings")
    assert main(args) == 0 and load_profile(output) == profile
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert main(args) == 2
    assert "Body 0" not in capsys.readouterr().out
    report = build_run_report(profile, inputs[0], validate_candidate(inputs[0]))

    class Engine:
        def dispose(self):
            pass

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setattr(cli_index_jobs, "index_engine", lambda _: Engine())
    monkeypatch.setattr(cli_index_jobs, "read_run_report", lambda *a: report)
    export = tmp_path / "report.json"
    argv = ["knowledge-index-report", "--run-id", "run-" + "a" * 32, "--output", str(export)]
    assert main(argv) == 0 and stat.S_IMODE(export.stat().st_mode) == 0o600
    assert json.loads(export.read_text())["report_id"] == report.report_id
    original = export.read_bytes()
    assert main(argv) == 2 and export.read_bytes() == original
    output = capsys.readouterr()
    assert "Body 0" not in output.out + output.err


def test_golden_migration_has_literal_sql_and_preserves_history_on_downgrade(monkeypatch):
    path = ROOT / "src/retailops_ai/migrations/versions/0007_rag_golden_jobs.py"
    spec = importlib.util.spec_from_file_location("golden_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    monkeypatch.setattr(op, "execute", statements.append)
    module.upgrade()
    assert statements
    for statement in statements:
        assert text(statement).compile().params == {}

    class Connection:
        def scalar(self, _):
            return True

    monkeypatch.setattr(op, "get_bind", lambda: Connection())
    with pytest.raises(RuntimeError, match="requires_empty_history"):
        module.downgrade()
