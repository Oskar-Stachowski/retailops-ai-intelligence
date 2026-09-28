"""Real pgvector and authenticated loopback checks on a separate adversarial fixture corpus."""

import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from verify_rag_lifecycle import require, synthetic_approval

from retailops_ai.adapters.index_lifecycle import current_index, qualify_index, switch_index
from retailops_ai.adapters.knowledge_search import PostgresKnowledge, deny_document
from retailops_ai.adapters.vector_store import index_engine, store_candidate
from retailops_ai.cli_knowledge import DEFAULT_CONFIG
from retailops_ai.config import load_settings
from retailops_ai.domain.access import KnowledgeAccess, Principal
from retailops_ai.knowledge.contracts import REPOSITORIES, CorpusRegistry, Repository
from retailops_ai.knowledge.indexes import IndexCandidate
from retailops_ai.knowledge.releases import SwitchRequest
from retailops_ai.knowledge.retrieval import DocumentDenial, RetrievalRequest, RetrievalResult
from retailops_ai.pipelines.chunks import build_chunks, load_chunker_config
from retailops_ai.pipelines.indexes import build_index, load_embedding_config
from retailops_ai.pipelines.releases import validate_candidate
from retailops_ai.pipelines.retrieval import (
    KnowledgeDenied,
    load_retrieval_config,
    search_candidate,
)
from retailops_ai.security.local import token_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def retrieval_candidate(root: Path) -> IndexCandidate:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git_unavailable")

    def run(repo: Path, *args: str) -> str:
        return (
            subprocess.check_output([git, "-C", str(repo), *args], stderr=subprocess.DEVNULL)  # noqa: S603 - controlled fixture Git
            .decode()
            .strip()
        )  # noqa: S603 - controlled fixture Git

    sources = []
    repos: dict[Repository, Path] = {}
    attacks = json.loads((ROOT / "tests/fixtures/rag/adversarial.v1.json").read_text())["documents"]
    for i, repository in enumerate(REPOSITORIES):
        repo = root / str(i)
        (repo / "docs").mkdir(parents=True)
        run(repo, "init", "-q")
        run(repo, "config", "user.name", "Fixture author")
        run(repo, "config", "user.email", "fixture@example.invalid")
        run(repo, "remote", "add", "origin", f"https://github.com/{repository}.git")
        documents: list[dict[str, object]] = []
        specimens = [
            ("public", "public_project", "specified", "Shared body."),
            ("internal", "project_internal", "specified", "Shared body."),
            ("restricted", "restricted", "specified", "Shared body."),
            ("old", "public_project", "historical", "Shared body."),
        ]
        if i == 0:
            specimens.extend(
                (
                    d["path"].removeprefix("docs/").removesuffix(".md"),
                    "public_project",
                    "specified",
                    d["body"],
                )
                for d in attacks
            )
        for name, access, status, body in specimens:
            raw = f"# {name}\n\n## Scope\n\n{body}\n".encode()
            path = f"docs/{name}.md"
            (repo / path).write_bytes(raw)
            documents.append(
                {
                    "path": path,
                    "title": name,
                    "byte_sha256": hashlib.sha256(raw).hexdigest(),
                    "document_type": "guide",
                    "document_status": status,
                    "access_class": access,
                    "fact_scope": "Synthetic retrieval security and conflict fixture only.",
                    "implementation_refs": [],
                    "verification": None,
                }
            )
        run(repo, "add", ".")
        run(repo, "commit", "-qm", "Synthetic retrieval corpus")
        sources.append(
            {
                "repository": repository,
                "commit_sha": run(repo, "rev-parse", "HEAD"),
                "allowed_roots": ["docs"],
                "documents": documents,
            }
        )
        repos[repository] = repo
    registry = CorpusRegistry.model_validate_json(
        json.dumps(
            {
                "schema_version": "1.0",
                "policy_version": "registered-markdown-v1",
                "environment": "test",
                "review_state": "proposed",
                "review_owner": "fixture-maintainer",
                "sources": sources,
            }
        )
    )
    return build_index(
        build_chunks(registry, load_chunker_config(ROOT / "knowledge/chunker.v1.json"), repos),
        load_embedding_config(ROOT / "knowledge/embeddings.fake.v1.json"),
    )


def verify_retrieval(candidate: IndexCandidate) -> dict[str, object]:
    engine = index_engine(load_settings())
    config = load_retrieval_config(DEFAULT_CONFIG)
    backend = PostgresKnowledge(engine, "test", config)
    checks = []
    times: list[float] = []
    saved = current_index(engine, "test", "offline_test")
    if saved is None:
        raise RuntimeError("lifecycle_pin_missing")

    def request(question: str = "Shared body.", **changes: object) -> RetrievalRequest:
        return RetrievalRequest.model_validate_json(
            json.dumps({"schema_version": "1.0", "question": question, **changes})
        )

    def reader(
        classes: frozenset[str] = frozenset({"public_project"}),
        statuses: frozenset[str] = frozenset(
            {"specified", "historical", "verified", "implemented"}
        ),
    ) -> Principal:
        return Principal(
            "fixture-reader",
            frozenset({"operator"}),
            frozenset({"knowledge:read"}),
            frozenset(),
            frozenset(),
            frozenset(),
            KnowledgeAccess("test", frozenset(REPOSITORIES), classes, statuses),
        )

    def query(body: RetrievalRequest, principal: Principal) -> RetrievalResult:
        start = time.monotonic()
        result = backend.search_pinned(pin, body, principal)
        times.append((time.monotonic() - start) * 1000)
        return result

    try:
        store_candidate(engine, candidate)
        qualify_index(
            engine,
            candidate.manifest.index_id,
            "test",
            "offline_test",
            synthetic_approval(candidate),
            validate_candidate(candidate),
        )
        change = SwitchRequest(
            schema_version="1.0",
            request_id="rag-change-" + secrets.token_hex(16),
            environment="test",
            lane="offline_test",
            operation="activate",
            target_index_id=candidate.manifest.index_id,
            expected_generation=saved.generation,
            actor="fixture-promoter",
        )
        pin = switch_index(engine, change).pin
        public = reader()
        first = query(request(), public)
        expected = search_candidate(candidate, request(), public, config)
        require(
            [h.chunk.chunk_id for h in first.items] == [h.chunk.chunk_id for h in expected.items],
            "sql_and_offline_rank_disagree",
        )
        require(bool(first.items) and first.items[0].score > 0.999999, "sql_exact_cosine_failed")
        require(
            all(
                h.chunk.access_class == "public_project" and h.chunk.document_status != "historical"
                for h in first.items
            ),
            "sql_leaked_class_or_status",
        )
        require(len({h.chunk.repository for h in first.items}) == 2, "sql_source_diversity_failed")
        require(
            first.context_tokens <= 6000 and first.context_bytes <= 24000,
            "sql_context_budget_failed",
        )
        privileged = query(
            request(), reader(frozenset({"public_project", "project_internal", "restricted"}))
        )
        require(
            any(h.chunk.access_class != "public_project" for h in privileged.items),
            "privileged_fixture_not_retrieved",
        )
        repeated = query(request(), public)
        require(
            all(h.chunk.access_class == "public_project" for h in repeated.items),
            "repeated_search_leaked_privileged_hits",
        )
        try:
            query(request(filters={"access_classes": ["restricted"]}), public)
        except KnowledgeDenied:
            pass
        else:
            raise RuntimeError("sql_scope_expansion_accepted")
        one = query(request(filters={"repositories": [REPOSITORIES[1]]}), public)
        require(
            all(h.chunk.repository == REPOSITORIES[1] for h in one.items),
            "sql_repository_filter_failed",
        )
        history = query(request(purpose="history"), public)
        require(
            bool(history.items)
            and all(h.chunk.document_status == "historical" for h in history.items),
            "sql_history_filter_failed",
        )
        require(
            query(request(purpose="implementation"), public).status == "insufficient_evidence",
            "sql_plan_claimed_as_implemented",
        )
        require(
            query(request(filters={"document_types": ["model_card"]}), public).status
            == "insufficient_evidence",
            "sql_type_filter_failed",
        )
        require(
            query(request(max_context_tokens=1), public).status == "insufficient_evidence",
            "sql_tiny_context_budget_failed",
        )
        checks.append("real_exact_cosine_ties_acl_repo_type_status_history_diversity_and_budgets")
        attack = next(c for c in candidate.chunks.chunks if c.path == "docs/attack.md")
        attacked = query(request(attack.text), public)
        require(
            attacked.items[0].chunk == attack
            and attacked.content_trust == "untrusted_reference"
            and attacked.answer_generation == "not_implemented",
            "sql_injection_reference_boundary_failed",
        )
        checks.append("adversarial_source_keeps_untrusted_metadata_without_tool_or_role_effects")

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            token = secrets.token_urlsafe(32)
            admin = secrets.token_urlsafe(32)
            now = datetime.now(UTC)
            scope = {
                "environment": "test",
                "repositories": list(REPOSITORIES),
                "access_classes": ["public_project"],
                "document_statuses": ["specified", "verified", "implemented"],
            }
            policy = {
                "schema_version": "1.0",
                "policy_id": "retrieval-loopback-fixture",
                "grants": [
                    {
                        "principal_id": "fixture-reader",
                        "roles": ["operator"],
                        "capabilities": ["knowledge:read"],
                        "scope": None,
                        "knowledge_scope": scope,
                    },
                    {
                        "principal_id": "fixture-admin",
                        "roles": ["admin"],
                        "capabilities": ["access:admin"],
                        "scope": None,
                    },
                ],
                "credentials": [
                    {
                        "principal_id": p,
                        "token_sha256": token_fingerprint(t),
                        "not_before": (now - timedelta(seconds=30)).isoformat(),
                        "expires_at": (now + timedelta(hours=1)).isoformat(),
                        "revoked": False,
                    }
                    for p, t in [("fixture-reader", token), ("fixture-admin", admin)]
                ],
            }
            policy_path = folder / "policy.json"
            policy_path.write_text(json.dumps(policy))
            policy_path.chmod(0o600)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            cli = shutil.which("retailops-ai")
            if cli is None:
                raise RuntimeError("knowledge_cli_unavailable")
            env = {
                **os.environ,
                "APP_ENV": "test",
                "ARTIFACT_ROOT": str(folder),
                "API_AUTH_FILE": str(policy_path),
                "NETWORK_MODE": "local",
                "HTTP_HOST": "127.0.0.1",
                "HTTP_PORT": str(port),
            }
            child = subprocess.Popen(  # noqa: S603 - fixed local service and fixture credentials
                [cli, "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )  # noqa: S603 - fixed local service and fixture credentials

            def http(
                body: dict[str, object], credential: str | None
            ) -> tuple[int, dict[str, Any], Message]:
                headers = {"Content-Type": "application/json"}
                if credential is not None:
                    headers["Authorization"] = "Bearer " + credential
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/v1/knowledge/search",
                    data=json.dumps(body).encode(),
                    headers=headers,
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=5) as response:  # noqa: S310 - fixed loopback HTTP
                        return response.status, json.loads(response.read()), response.headers
                except urllib.error.HTTPError as error:
                    return error.code, json.loads(error.read()), error.headers

            try:
                body = request().model_dump(mode="json")
                for _ in range(80):
                    try:
                        status, parsed, headers = http(body, token)
                        break
                    except urllib.error.URLError:
                        time.sleep(0.05)
                else:
                    raise RuntimeError("knowledge_loopback_not_started")
                require(
                    status == 200 and headers.get("Cache-Control") == "no-store",
                    "knowledge_loopback_search_failed",
                )
                for supplied, changed, status in [
                    (None, body, 401),
                    (admin, body, 403),
                    (token, {**body, "role": "admin"}, 422),
                    (token, {**body, "filters": {"access_classes": ["restricted"]}}, 403),
                ]:
                    code, error, headers = http(changed, supplied)
                    require(
                        code == status
                        and token not in json.dumps(error)
                        and headers.get("Cache-Control") == "no-store",
                        "knowledge_loopback_authorization_failed",
                    )
                denied_id = first.items[0].chunk.document_id
                denial = DocumentDenial(
                    schema_version="1.0",
                    environment="test",
                    document_id=denied_id,
                    actor="fixture-admin",
                    reason="fixture_revocation",
                )
                require(
                    deny_document(engine, denial) and not deny_document(engine, denial),
                    "live_denial_not_idempotent",
                )
                code, parsed, _ = http(body, token)
                require(
                    code == 200
                    and all(h["chunk"]["document_id"] != denied_id for h in parsed["items"]),
                    "warm_http_search_bypassed_live_denial",
                )
            finally:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        checks.append(
            "real_loopback_http_credentials_401_403_422_no_store_and_live_denial_after_warm_read"
        )
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM ai.rag_document_denials WHERE environment='test'")
                )
        except DBAPIError as error:
            require(
                getattr(error.orig, "sqlstate", None) == "23514",
                "unexpected_denial_mutation_failure",
            )
        else:
            raise RuntimeError("document_denial_deleted")
        restored = switch_index(
            engine,
            SwitchRequest(
                schema_version="1.0",
                request_id="rag-change-" + secrets.token_hex(16),
                environment="test",
                lane="offline_test",
                operation="rollback",
                target_index_id=saved.manifest.index_id,
                expected_generation=pin.generation,
                actor="fixture-promoter",
            ),
        ).pin
        old = query(request(), public)
        require(
            all(h.chunk.document_id != denied_id for h in old.items), "old_pin_bypassed_live_denial"
        )
        require(
            old.index_id == candidate.manifest.index_id and restored.manifest == saved.manifest,
            "search_pin_mixed_current_version",
        )
        checks.append("immutable_live_denial_covers_old_pin_after_swap_and_prior_manifest_restored")
        return {
            "result": "passed",
            "checks": checks,
            "fixture_documents": len(candidate.chunks.corpus.documents),
            "fixture_chunks": len(candidate.chunks.chunks),
            "max_sql_search_ms": max(times),
            "final_pin": restored.model_dump(mode="json"),
            "actual_corpus_activated": False,
            "semantic_quality_evaluated": False,
        }
    finally:
        engine.dispose()
