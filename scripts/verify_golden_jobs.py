"""Real local HTTP/PG golden runs, failed reports, SQL gates and immutable replay."""

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from verify_rag_lifecycle import require

from retailops_ai.adapters import index_jobs as jobs
from retailops_ai.adapters.vector_store import index_engine
from retailops_ai.cli_knowledge import DEFAULT_CONFIG
from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.golden import GoldenSet
from retailops_ai.knowledge.indexes import MAX_INDEX_BYTES, IndexCandidate
from retailops_ai.knowledge.jobs import GoldenIndexBuildProfile, GoldenIndexRunReport
from retailops_ai.knowledge.qualification import GoldenLabelsApproval, SimilarityReview
from retailops_ai.knowledge.releases import CorpusApproval
from retailops_ai.knowledge.retrieval import RetrievalRequest
from retailops_ai.pipelines.index_builds import prepare_build_profile
from retailops_ai.pipelines.retrieval import load_retrieval_config
from retailops_ai.pipelines.review import review_similarity
from retailops_ai.security.local import token_fingerprint


def fixture_profile(candidate: IndexCandidate, *, fail: bool = False) -> GoldenIndexBuildProfile:
    first = candidate.chunks.chunks[0]
    corpus = candidate.chunks.corpus
    config = load_retrieval_config(DEFAULT_CONFIG)
    categories = (
        "documentation",
        "models",
        "operations",
        "missing",
        "conflict",
        "injection",
        "authorization",
    )
    cases = []
    for i in range(30):
        cases.append(
            {
                "case_id": f"golden-fixture-{i}",
                "category": categories[i % 7],
                "request": RetrievalRequest(schema_version="1.0", question=first.text).model_dump(
                    mode="json"
                ),
                "role": "operator",
                "scope": {
                    "environment": corpus.environment,
                    "repositories": [s.repository for s in corpus.sources],
                    "access_classes": ["public_project"],
                    "document_statuses": ["specified"],
                },
                "expected_sections": [
                    {
                        "repository": first.repository,
                        "path": first.path,
                        "heading_path": [h.title for h in first.heading_path],
                        "document_status": "specified",
                    }
                ],
                "forbidden_sources": [],
                "answerability": "answerable",
                "acceptable_outcomes": ["forbidden" if fail and i == 0 else "ok"],
                "required_tools": ["search_knowledge"],
                "forbidden_tools": ["knowledge_index_activate"],
                "critical": i == 0,
            }
        )
    v: dict[str, Any] = {
        "schema_version": "1.0",
        "set_version": "retailops-rag-golden-v1",
        "index_id": candidate.manifest.index_id,
        "retrieval_config_id": config.config_id(),
        "review_state": "proposed",
        "review_owner": corpus.review_owner,
        "labels_origin": "manually_authored_source_sections_not_ranker_output",
        "thresholds": {
            "recall_at_5_min": 0.8,
            "mrr_min": 0.6,
            "citation_correctness_min": 1.0,
            "critical_pass_rate_min": 1.0,
            "groundedness_min": 0.95,
            "latency_p95_ms_max": 1000.0,
            "offline_cost_usd_max": 0.0,
        },
        "cases": cases,
    }
    v["golden_set_id"] = "golden-set-sha256-" + canonical_sha256(v)
    golden = GoldenSet.model_validate_json(json.dumps(v))
    common = {
        "schema_version": "1.0",
        "environment": corpus.environment,
        "review_owner": corpus.review_owner,
        "reviewer": "fixture-approved-pipeline",
        "reviewer_kind": "approved_pipeline",
        "decision": "approved",
        "reviewed_at": "2026-09-28T15:00:00Z",
    }
    v = {
        **common,
        "corpus_id": corpus.corpus_id,
        "corpus_config_id": corpus.corpus_config_id,
        "scope": "sources_status_access_and_exclusions",
    }
    v["review_id"] = "corpus-review-sha256-" + canonical_sha256(v)
    approval = CorpusApproval.model_validate_json(json.dumps(v))
    v = {
        **common,
        "golden_set_id": golden.golden_set_id,
        "index_id": golden.index_id,
        "retrieval_config_id": golden.retrieval_config_id,
        "scope": "labels_tools_and_frozen_thresholds",
    }
    v["approval_id"] = "golden-approval-sha256-" + canonical_sha256(v)
    labels = GoldenLabelsApproval.model_validate_json(json.dumps(v))
    # Explicit bounded policy, identical to the committed offline policy.
    from retailops_ai.knowledge.review import SimilarityPolicy

    policy = SimilarityPolicy(
        schema_version="1.0",
        algorithm="retrievable-word-trigram-jaccard-v1",
        normalization="nfkc-casefold-unicode-words-v1",
        unicode_version="14.0.0",
        shingle_words=3,
        minimum_words=12,
        threshold_bps=8000,
        max_features=250000,
        max_candidate_pairs=250000,
        max_posting_visits=2000000,
        max_report_pairs=10000,
    )
    similarity = review_similarity(candidate.chunks, policy)
    require(similarity.outcome == "no_lexical_candidates", "golden_fixture_unreviewed_duplicates")
    v = {
        "schema_version": "1.0",
        "report_id": similarity.report_id,
        "review_owner": corpus.review_owner,
        "reviewer": "fixture-reviewer",
        "review_kind": "technical_lexical_review_only",
        "reviewed_at": "2026-09-28T15:00:00Z",
        "corpus_approval_created": False,
        "decisions": [],
    }
    v["review_id"] = "similarity-review-sha256-" + canonical_sha256(v)
    review = SimilarityReview.model_validate_json(json.dumps(v))
    return prepare_build_profile(candidate, golden, approval, labels, config, policy, review)


def verify_profiles(profiles: list[GoldenIndexBuildProfile]) -> dict[str, Any]:
    engine = index_engine(load_settings())
    admin = jobs.PostgresIndexAdministration(engine, "local")
    retained = []
    reports = []
    cli = shutil.which("retailops-ai")
    require(cli is not None, "golden_cli_missing")
    try:
        require(admin.current() is None, "golden_actual_pointer_was_active")
        for profile in profiles:
            jobs.register_profile(engine, profile)
            require(not jobs.register_profile(engine, profile), "golden_profile_replay_changed")
        with tempfile.TemporaryDirectory(prefix="golden-http-") as directory:
            folder = Path(directory)
            token = secrets.token_urlsafe(32)
            now = datetime.now(UTC)
            policy = {
                "schema_version": "1.0",
                "policy_id": "golden-http-fixture",
                "grants": [
                    {
                        "principal_id": "fixture-golden-admin",
                        "roles": ["admin"],
                        "capabilities": ["knowledge:index"],
                        "scope": None,
                    }
                ],
                "credentials": [
                    {
                        "principal_id": "fixture-golden-admin",
                        "token_sha256": token_fingerprint(token),
                        "not_before": (now - timedelta(seconds=30)).isoformat(),
                        "expires_at": (now + timedelta(hours=1)).isoformat(),
                        "revoked": False,
                    }
                ],
            }
            auth = folder / "policy.json"
            auth.write_text(json.dumps(policy))
            auth.chmod(0o600)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            child = subprocess.Popen(  # noqa: S603 - fixed loopback CLI
                [str(cli), "serve"],
                env={
                    **os.environ,
                    "APP_ENV": "local",
                    "ARTIFACT_ROOT": str(folder),
                    "API_AUTH_FILE": str(auth),
                    "NETWORK_MODE": "local",
                    "HTTP_HOST": "127.0.0.1",
                    "HTTP_PORT": str(port),
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )  # noqa: S603 - controlled loopback

            def http(
                route: str,
                body: dict[str, Any] | None = None,
                key: str | None = None,
                authenticated: bool = True,
            ) -> tuple[int, dict[str, Any]]:
                headers = {"Content-Type": "application/json"}
                if authenticated:
                    headers["Authorization"] = "Bearer " + token
                if key:
                    headers["Idempotency-Key"] = key
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/v1/" + route,
                    data=json.dumps(body).encode() if body else None,
                    headers=headers,
                )
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - loopback only
                        return response.status, json.loads(response.read())
                except urllib.error.HTTPError as error:
                    return error.code, json.loads(error.read())

            try:
                for _ in range(600):
                    require(child.poll() is None, "golden_http_process_exited")
                    try:
                        http("knowledge-indexes/current")
                        break
                    except urllib.error.URLError:
                        time.sleep(0.05)
                else:
                    raise RuntimeError("golden_http_not_started")
                for profile in profiles:
                    body = profile.request().model_dump(mode="json")
                    key = "golden-" + secrets.token_hex(8)
                    require(
                        http("knowledge-index-runs", body, key, False)[0] == 401,
                        "golden_auth_bypassed",
                    )
                    code, queued = http("knowledge-index-runs", body, key)
                    require(code == 202, "golden_local_http_rejected")
                    run_id = queued["run_id"]
                    finished = jobs.execute_run(engine, "local", run_id)
                    report = jobs.read_run_report(engine, "local", run_id)
                    if not isinstance(report, GoldenIndexRunReport):
                        raise RuntimeError("golden_wrong_report_kind")
                    require(
                        finished.status
                        == ("succeeded" if report.quality_gate_passed else "failed"),
                        "golden_gate_did_not_control_state",
                    )
                    require(
                        report.activation_allowed is False and admin.current() is None,
                        "golden_run_activated",
                    )
                    if finished.status == "failed":
                        require(
                            finished.error is not None
                            and finished.error.code == "gate_failed"
                            and finished.output_ref is None,
                            "golden_failed_output",
                        )
                    require(
                        http("knowledge-index-runs/" + run_id)[1]
                        == finished.model_dump(mode="json"),
                        "golden_http_run_get_changed",
                    )
                    require(
                        http("knowledge-index-runs", body, key)[1]
                        == finished.model_dump(mode="json"),
                        "golden_terminal_http_replay_changed",
                    )
                    require(
                        jobs.execute_run(engine, "local", run_id) == finished,
                        "golden_worker_retry_changed",
                    )
                    require(
                        jobs.read_run_report(engine, "local", run_id) == report,
                        "golden_report_retry_changed",
                    )
                    export = folder / (run_id + ".json")
                    result = subprocess.run(  # noqa: S603 - fixed controlled CLI
                        [
                            str(cli),
                            "knowledge-index-report",
                            "--run-id",
                            run_id,
                            "--output",
                            str(export),
                        ],
                        env={**os.environ, "APP_ENV": "local"},
                        capture_output=True,
                        text=True,
                    )  # noqa: S603
                    require(
                        result.returncode == 0 and export.stat().st_mode & 0o777 == 0o600,
                        "golden_report_export_failed",
                    )
                    require(
                        json.loads(export.read_text()) == report.model_dump(mode="json"),
                        "golden_export_differed",
                    )
                    retained.append(finished.model_dump(mode="json"))
                    reports.append(report.model_dump(mode="json"))
                try:
                    jobs.PostgresIndexAdministration(engine, "test").submit(
                        profiles[0].request(),
                        "fixture-golden-admin",
                        "outside-" + secrets.token_hex(8),
                    )
                except jobs.IndexJobError as error:
                    require(error.status == 422, "golden_wrong_environment_error")
                else:
                    raise RuntimeError("golden_environment_expanded")
            finally:
                child.terminate()
                child.wait(timeout=10)
        # SQL cannot lower a frozen threshold or turn only the summary booleans into success.
        for profile, raw_report in zip(profiles, reports, strict=True):
            if raw_report["quality_gate_passed"]:
                continue
            forged = json.loads(json.dumps(raw_report))
            forged["quality_gate_passed"] = True
            forged["golden"]["measured_thresholds_passed"] = True
            forged.pop("report_id")
            forged["report_id"] = "index-run-report-sha256-" + canonical_sha256(forged)
            probe = admin.submit(
                profile.request(), "fixture-golden-admin", "sql-" + secrets.token_hex(8)
            )
            from retailops_ai.data_contracts.run import RunRecord

            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ai.rag_index_reports(report_id,profile_id,index_id,report) VALUES (:id,:profile,:index,CAST(:report AS jsonb))"
                    ),
                    {
                        "id": forged["report_id"],
                        "profile": profile.profile_id,
                        "index": raw_report["validation"]["index_id"],
                        "report": json.dumps(forged),
                    },
                )
                raw = probe.model_dump(mode="json")
                raw.update(status="running", started_at=datetime.now(UTC).isoformat())
                raw = RunRecord.model_validate_json(json.dumps(raw)).model_dump(mode="json")
                connection.execute(
                    text(
                        "UPDATE ai.knowledge_index_runs SET record=CAST(:record AS jsonb),claim_token=CAST(:claim AS uuid) WHERE run_id=:id"
                    ),
                    {
                        "id": probe.run_id,
                        "record": json.dumps(raw),
                        "claim": "00000000-0000-4000-8000-000000000001",
                    },
                )
            raw.update(
                status="succeeded",
                completed_at=datetime.now(UTC).isoformat(),
                output_ref={
                    "kind": "knowledge_index",
                    "complete": True,
                    "index_id": raw_report["validation"]["index_id"],
                    "manifest_ref": "db:ai.rag_indexes:" + raw_report["validation"]["index_id"],
                    "evaluation_report_ref": "db:ai.rag_index_reports:" + forged["report_id"],
                    "activation_status": "candidate",
                },
            )
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE ai.knowledge_index_runs SET record=CAST(:record AS jsonb),claim_token=NULL,output_index_id=:index,report_id=:report WHERE run_id=:id"
                        ),
                        {
                            "id": probe.run_id,
                            "record": json.dumps(raw),
                            "index": raw_report["validation"]["index_id"],
                            "report": forged["report_id"],
                        },
                    )
            except DBAPIError as error:
                require(
                    getattr(error.orig, "sqlstate", None) == "23514", "golden_wrong_gate_sqlstate"
                )
            else:
                raise RuntimeError("golden_sql_fake_pass_accepted")
            jobs.cancel_run(engine, "local", probe.run_id)
        return {
            "result": "passed",
            "checks": [
                "local_http_submission_read_and_terminal_idempotency",
                "approved_profile_and_golden_pins",
                "golden_gate_controls_success_and_failed_report_retention",
                "private_cli_report_export",
                "sql_summary_forgery_rejected",
                "no_user_pointer_activation",
            ],
            "retained_runs": retained,
            "reports": reports,
            "actual_corpus_activated": False,
        }
    finally:
        engine.dispose()


def verify_golden_administration(candidate: IndexCandidate) -> dict[str, Any]:
    result = verify_profiles([fixture_profile(candidate), fixture_profile(candidate, fail=True)])
    require(
        [r["status"] for r in result["retained_runs"]] == ["succeeded", "failed"],
        "golden_fixture_gate_outcomes_wrong",
    )
    return result


if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["--approved-profile"]:
            raise RuntimeError("unsupported_golden_verification")
        raw = sys.stdin.read(MAX_INDEX_BYTES + 1)
        require(len(raw.encode()) <= MAX_INDEX_BYTES, "golden_profile_size_limit")
        profile = GoldenIndexBuildProfile.model_validate_json(raw)
        print(json.dumps(verify_profiles([profile])))
    except Exception as error:
        print(
            json.dumps({"error": "approved_golden_run_failed", "type": type(error).__name__}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
