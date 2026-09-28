import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_access import bearer, policy_file, problem
from test_chunks import build, git
from test_chunks import config as config
from test_chunks import sources as sources
from test_index_lifecycle import approval
from test_indexes import embedding_config as embedding_config

from retailops_ai import cli_index_jobs
from retailops_ai.adapters.index_jobs import IndexJobError, worker_lock_key
from retailops_ai.api.app import create_app
from retailops_ai.cli import main
from retailops_ai.cli_index_jobs import load_profile
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.registry import validate_document
from retailops_ai.data_contracts.run import MLRunRecord, RunRecord, transition_run
from retailops_ai.knowledge.jobs import (
    CurrentKnowledgeIndex,
    IndexBuildProfile,
    KnowledgeIndexRequest,
    KnowledgeRunInput,
)
from retailops_ai.pipelines.indexes import build_index
from retailops_ai.security.models import GrantTemplate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile(sources, config, embedding_config):
    candidate = build_index(build(sources, config), embedding_config)
    raw = {
        "schema_version": "1.0",
        "environment": "test",
        "approval": approval(candidate).model_dump(mode="json"),
        "chunks": candidate.chunks.model_dump(mode="json"),
        "embedding_config": embedding_config.model_dump(mode="json"),
        "evaluation_set_id": "offline-index-mechanics-v1",
        "purpose": "offline_build_mechanics_only",
    }
    raw["profile_id"] = "index-build-profile-sha256-" + canonical_sha256(raw)
    return IndexBuildProfile.model_validate_json(json.dumps(raw))


def queued(profile):
    request = profile.request()
    return RunRecord(
        schema_version="1.0",
        contract_type="run",
        run_type="knowledge_index",
        run_id="run-" + "a" * 32,
        status="queued",
        attempt=1,
        requested_at=datetime.now(UTC),
        requested_by="local-admin",
        started_at=None,
        completed_at=None,
        resolved_model=None,
        output_ref=None,
        error=None,
        input_ref=KnowledgeRunInput(
            request=request,
            request_hash=request.request_hash(),
            profile_id=profile.profile_id,
            environment="test",
        ),
    )


class Backend:
    def __init__(self, profile):
        self.profile = profile
        self.calls = []
        self.run = queued(profile)
        self.failure = None

    def submit(self, request, principal, key):
        self.calls.append((request, principal, key))
        if self.failure:
            raise self.failure
        return self.run

    def get(self, run_id):
        self.calls.append(run_id)
        if run_id != self.run.run_id:
            raise IndexJobError(404, "index-run-not-found")
        return self.run

    def current(self):
        self.calls.append("current")
        return None


def admin_client(tmp_path, backend, grant=True):
    def change(value):
        if grant:
            next(g for g in value["grants"] if g["principal_id"] == "local-admin")[
                "capabilities"
            ].append("knowledge:index")

    path, tokens = policy_file(tmp_path, change)
    app = create_app(
        Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path),
        index_administration=backend,
    )
    return TestClient(app, base_url="http://127.0.0.1"), tokens


def test_approved_profile_and_shared_run_schemas_bind_all_inputs(profile):
    run = queued(profile)
    assert run.run_type == "knowledge_index"
    assert run.input_ref.profile_id == profile.profile_id
    for name, value in (
        ("index-build-profile", profile),
        ("knowledge-index-request", profile.request()),
    ):
        schema = json.loads((ROOT / f"contracts/knowledge/v1/{name}.v1.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(value.model_dump(mode="json"))
    schema = json.loads(
        (ROOT / "contracts/knowledge/v1/knowledge-index-run.v1.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(run.model_dump(mode="json"))
    original = json.loads((ROOT / "contracts/intelligence/v1/run.v1.schema.json").read_text())
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(original).validate(run.model_dump(mode="json"))
    with pytest.raises(ValidationError):
        validate_document("run", run.model_dump_json().encode())
    reversed_sources = profile.request().model_copy(
        update={"sources": tuple(reversed(profile.request().sources))}
    )
    assert reversed_sources.request_hash() == profile.request().request_hash()
    with pytest.raises(ValidationError):
        MLRunRecord.model_validate_json(run.model_dump_json())


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_source",
        "duplicate_source",
        "placeholder",
        "short_sha",
        "url",
        "path",
        "model",
        "prompt",
        "dimension",
        "extra_identity",
    ],
)
def test_request_cannot_choose_untrusted_sources_or_runtime_configuration(profile, tamper):
    raw = profile.request().model_dump(mode="json")
    if tamper == "missing_source":
        raw["sources"].pop()
    elif tamper == "duplicate_source":
        raw["sources"][1] = raw["sources"][0]
    elif tamper in {"placeholder", "short_sha"}:
        raw["sources"][0]["commit_sha"] = "0" * 40 if tamper == "placeholder" else "abc123"
    elif tamper == "url":
        raw["sources"][0]["repository"] = "https://private-marker.invalid"
    else:
        raw[tamper] = "private-marker"
    with pytest.raises(ValidationError):
        KnowledgeIndexRequest.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("tamper", ["environment", "approval", "identity", "quality_policy"])
def test_profile_cannot_approve_local_corpus_or_semantic_quality(profile, tamper):
    raw = profile.model_dump(mode="json")
    if tamper == "environment":
        raw["environment"] = "local"
    elif tamper == "approval":
        raw["approval"]["decision"] = "proposed"
    elif tamper == "identity":
        raw["chunks"]["chunk_manifest_id"] = "chunks-sha256-" + "f" * 64
    else:
        raw["evaluation_set_id"] = "semantic-quality-passed"
    raw["profile_id"] = "index-build-profile-sha256-" + canonical_sha256(
        {k: v for k, v in raw.items() if k != "profile_id"}
    )
    with pytest.raises(ValidationError):
        IndexBuildProfile.model_validate_json(json.dumps(raw))


def test_shared_run_enforces_pins_state_and_output_type(profile):
    first = queued(profile)
    raw = first.model_dump(mode="json")
    raw.update(status="running", started_at=datetime.now(UTC).isoformat())
    running = transition_run(first, RunRecord.model_validate_json(json.dumps(raw)))
    raw.update(
        status="succeeded",
        completed_at=datetime.now(UTC).isoformat(),
        output_ref={
            "kind": "knowledge_index",
            "complete": True,
            "index_id": "index-sha256-" + "b" * 64,
            "manifest_ref": "db:ai.rag_indexes:index-sha256-" + "b" * 64,
            "evaluation_report_ref": "db:ai.rag_index_reports:index-run-report-sha256-" + "c" * 64,
            "activation_status": "candidate",
        },
    )
    succeeded = transition_run(running, RunRecord.model_validate_json(json.dumps(raw)))
    assert succeeded.output_ref.activation_status == "candidate"
    for field, value in (
        ("activation_status", "active"),
        ("manifest_ref", "db:ai.rag_indexes:index-sha256-" + "c" * 64),
        ("complete", 1),
    ):
        bad = deepcopy(raw)
        bad["output_ref"][field] = value
        with pytest.raises(ValidationError):
            RunRecord.model_validate_json(json.dumps(bad))
    bad = deepcopy(raw)
    bad["input_ref"]["request_hash"] = "d" * 64
    with pytest.raises(ValidationError, match="request_hash"):
        RunRecord.model_validate_json(json.dumps(bad))
    changed = succeeded.model_copy(update={"requested_by": "other-admin"})
    with pytest.raises(ValueError, match="pinned_input"):
        transition_run(running, changed)
    with pytest.raises(ValueError, match="illegal_run_transition"):
        transition_run(succeeded, running)


@pytest.mark.parametrize("principal", [None, "local-viewer", "local-operator", "local-admin"])
def test_auth_and_explicit_index_capability_precede_persistence(tmp_path, profile, principal):
    backend = Backend(profile)
    c, tokens = admin_client(tmp_path, backend, grant=False)
    headers = bearer(tokens[principal]) if principal else {}
    headers["Idempotency-Key"] = "fixture-request-1"
    with c:
        response = c.post(
            "/api/v1/knowledge-index-runs",
            json=profile.request().model_dump(mode="json"),
            headers=headers,
        )
        problem(response, 403 if principal else 401)
        for path in (
            "/api/v1/knowledge-index-runs/" + backend.run.run_id,
            "/api/v1/knowledge-indexes/current",
        ):
            problem(c.get(path, headers=headers), 403 if principal else 401)
    assert backend.calls == []


def test_http_submits_durable_run_location_and_safe_not_found(tmp_path, profile):
    backend = Backend(profile)
    c, tokens = admin_client(tmp_path, backend)
    headers = {**bearer(tokens["local-admin"]), "Idempotency-Key": "fixture-request-1"}
    with c:
        response = c.post(
            "/api/v1/knowledge-index-runs",
            json=profile.request().model_dump(mode="json"),
            headers=headers,
        )
        assert response.status_code == 202
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["location"] == "/api/v1/knowledge-index-runs/" + backend.run.run_id
        assert response.json() == backend.run.model_dump(mode="json")
        assert backend.calls == [(profile.request(), "local-admin", "fixture-request-1")]
        assert c.get(response.headers["location"], headers=headers).json() == response.json()
        missing = c.get("/api/v1/knowledge-index-runs/run-" + "b" * 32, headers=headers)
        problem(missing, 404)
        assert missing.json()["code"] == "index-run-not-found"
        current = c.get("/api/v1/knowledge-indexes/current", headers=headers)
        problem(current, 404)
        assert current.json()["code"] == "index-not-configured"
        problem(c.post("/api/v1/knowledge-indexes/activate", json={}, headers=headers), 404)


@pytest.mark.parametrize("key", [None, "", "a" * 129, "secret/key", "private marker"])
def test_invalid_idempotency_keys_are_rejected_without_persisting(tmp_path, profile, key):
    backend = Backend(profile)
    c, tokens = admin_client(tmp_path, backend)
    headers = bearer(tokens["local-admin"])
    if key is not None:
        headers["Idempotency-Key"] = key
    with c:
        problem(
            c.post(
                "/api/v1/knowledge-index-runs",
                json=profile.request().model_dump(mode="json"),
                headers=headers,
            ),
            422,
        )
    assert backend.calls == []


def test_duplicate_idempotency_header_is_rejected_before_persistence(tmp_path, profile):
    backend = Backend(profile)
    c, tokens = admin_client(tmp_path, backend)
    headers = [
        ("Authorization", "Bearer " + tokens["local-admin"]),
        ("Idempotency-Key", "fixture-1"),
        ("Idempotency-Key", "fixture-1"),
    ]
    with c:
        problem(
            c.post(
                "/api/v1/knowledge-index-runs",
                json=profile.request().model_dump(mode="json"),
                headers=headers,
            ),
            422,
        )
    assert backend.calls == []


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (IndexJobError(409, "idempotency-conflict"), 409, "idempotency-conflict"),
        (IndexJobError(422, "configuration-not-approved"), 422, "configuration-not-approved"),
        (IndexJobError(429, "queue-full"), 429, "queue-full"),
        (ValueError("private-marker"), 503, None),
    ],
)
def test_submit_errors_are_static_safe_problem_details(tmp_path, profile, failure, status, code):
    backend = Backend(profile)
    backend.failure = failure
    c, tokens = admin_client(tmp_path, backend)
    with c:
        response = c.post(
            "/api/v1/knowledge-index-runs",
            json=profile.request().model_dump(mode="json"),
            headers={**bearer(tokens["local-admin"]), "Idempotency-Key": "fixture-1"},
        )
        problem(response, status)
        assert response.json().get("code") == code
        assert "private-marker" not in response.text


@pytest.mark.parametrize("role", ["operator", "viewer"])
def test_index_capability_cannot_be_granted_to_ordinary_reader(role):
    with pytest.raises(ValidationError, match="admin_role"):
        GrantTemplate.model_validate(
            {
                "schema_version": "1.0",
                "policy_id": "fixture",
                "grants": [
                    {
                        "principal_id": "ordinary-reader",
                        "roles": [role],
                        "capabilities": ["knowledge:index"],
                        "scope": None,
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "raw", [b'{"private-marker":NaN}', b'{"schema_version":"1.0","schema_version":"1.0"}', b"\xff"]
)
def test_profile_loader_rejects_ambiguous_or_nonfinite_artifacts(tmp_path, raw):
    path = tmp_path / "profile.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        load_profile(path)


def test_profile_cli_rejects_environment_before_db_and_redacts_error(
    tmp_path, profile, monkeypatch, capsys
):
    path = tmp_path / "profile.json"
    path.write_text(profile.model_dump_json())
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert (
        main(
            [
                "knowledge-profile-register",
                "--profile",
                str(path),
                "--retailops-repo",
                "private-marker",
                "--ai-repo",
                "private-marker",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.err == '{"error":"knowledge_index_operation_failed"}\n'
    assert "private-marker" not in captured.out + captured.err


def test_worker_session_key_is_stable_and_distinguishes_environments():
    assert worker_lock_key("test", "run-" + "a" * 32) == worker_lock_key("test", "run-" + "a" * 32)
    assert worker_lock_key("test", "run-" + "a" * 32) != worker_lock_key("local", "run-" + "a" * 32)
    assert 0 <= worker_lock_key("test", "run-" + "a" * 32) < 2**63


@pytest.mark.parametrize("tamper", ["reference", "lane"])
def test_current_contract_cannot_mix_manifest_identity_or_claim_user_retrieval(profile, tamper):
    candidate = build_index(profile.chunks, profile.embedding_config)
    raw = {
        "index_id": candidate.manifest.index_id,
        "environment": "test",
        "lane": "offline_test",
        "purpose": "lifecycle_validation_only",
        "manifest_ref": "db:ai.rag_indexes:" + candidate.manifest.index_id,
        "corpus_manifest_id": candidate.manifest.corpus_id,
        "chunk_manifest_id": candidate.manifest.chunk_manifest_id,
        "index_config_id": profile.index_config_id(),
        "embedding_config_id": candidate.manifest.space_id,
        "dimension": candidate.manifest.embedding_config.dimension,
        "document_count": len(profile.chunks.corpus.documents),
        "chunk_count": candidate.manifest.chunk_count,
        "activated_at": "2026-09-28T10:00:00Z",
        "evaluation_report_ref": "db:ai.rag_qualifications:index-validation-sha256-" + "a" * 64,
    }
    current = CurrentKnowledgeIndex.model_validate_json(json.dumps(raw))
    schema = json.loads(
        (ROOT / "contracts/knowledge/v1/current-knowledge-index.v1.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(current.model_dump(mode="json"))
    if tamper == "reference":
        raw["manifest_ref"] = "db:ai.rag_indexes:index-sha256-" + "b" * 64
    else:
        raw["lane"] = "retrieval"
    with pytest.raises(ValidationError):
        CurrentKnowledgeIndex.model_validate_json(json.dumps(raw))


def test_missing_admin_database_is_503_after_successful_authentication(tmp_path, profile):
    c, tokens = admin_client(tmp_path, None)
    headers = {**bearer(tokens["local-admin"]), "Idempotency-Key": "fixture-unavailable"}
    with c:
        problem(
            c.post(
                "/api/v1/knowledge-index-runs",
                json=profile.request().model_dump(mode="json"),
                headers=headers,
            ),
            503,
        )
        problem(c.get("/api/v1/knowledge-index-runs/run-" + "a" * 32, headers=headers), 503)
        problem(c.get("/api/v1/knowledge-indexes/current", headers=headers), 503)


def test_profile_registration_checks_real_git_before_storage_and_ignores_worktree_edits(
    tmp_path, sources, profile, monkeypatch, capsys
):
    path = tmp_path / "profile.json"
    path.write_text(profile.model_dump_json())
    _, repositories = sources
    ordered = list(repositories.values())
    stored = []

    class Engine:
        def dispose(self):
            pass

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setattr(cli_index_jobs, "index_engine", lambda _: Engine())
    monkeypatch.setattr(
        cli_index_jobs, "register_profile", lambda _, value: stored.append(value) or True
    )
    argv = [
        "knowledge-profile-register",
        "--profile",
        str(path),
        "--retailops-repo",
        str(ordered[0]),
        "--ai-repo",
        str(ordered[1]),
    ]
    assert main(argv) == 0
    assert stored == [profile]
    (ordered[0] / "docs/guide.md").write_text("private-body-marker changed uncommitted")
    assert main(argv) == 0
    assert stored == [profile, profile]
    git(ordered[0], "remote", "set-url", "origin", "https://private-marker.invalid/unapproved.git")
    assert main(argv) == 2
    assert stored == [profile, profile]
    captured = capsys.readouterr()
    assert "private-marker" not in captured.out + captured.err
    assert "private-body-marker" not in captured.out + captured.err


def test_repeated_native_fixtures_keep_independent_document_denials(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from verify_knowledge_search import retrieval_candidate

    first = retrieval_candidate(tmp_path / "first")
    second = retrieval_candidate(tmp_path / "second")
    assert {d.document_id for d in first.chunks.corpus.documents}.isdisjoint(
        {d.document_id for d in second.chunks.corpus.documents}
    )
    assert {e.embedding_id for e in first.embeddings} == {e.embedding_id for e in second.embeddings}
