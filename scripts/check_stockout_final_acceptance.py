"""Actual final model review, MLflow/lifecycle and batch/API on an owned disposable runner."""

import argparse
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as xml
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import mlflow_store as store
from check_v12_lifecycle import IMAGES, ROOT, docker, wait_ready
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from v12_backup_fixture_stack import FixtureStack

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.domain.access import Principal
from retailops_ai.migrations.runner import migrate
from retailops_ai.security.local import LocalAccess, load_private_policy, token_fingerprint
from retailops_ai.security.models import AccessPolicy
from retailops_ai.source_snapshot.files import read_bytes, read_json
from retailops_ai.stockout_campaign.contract import CampaignFreeze, CampaignPermission
from retailops_ai.stockout_campaign.implementation import code_digest, lock_digest
from retailops_ai.stockout_campaign.report import aggregate
from retailops_ai.stockout_jobs.contracts import StockoutRequest
from retailops_ai.stockout_jobs.queue import PostgresStockoutQueue
from retailops_ai.stockout_jobs.reader import PostgresStockoutAdministration, PostgresStockoutReader
from retailops_ai.stockout_jobs.worker import preload, registry_guard, run_attempt
from retailops_ai.stockout_lifecycle.card import final_card
from retailops_ai.stockout_lifecycle.contract import (
    MODEL,
    REVIEW_GATES,
    ApprovalRequest,
    StockoutLifecycleRequest,
    StockoutQualification,
)
from retailops_ai.stockout_lifecycle.engine import StockoutLifecycle
from retailops_ai.stockout_lifecycle.evidence import verify_execution_evidence, verify_world_gates
from retailops_ai.stockout_lifecycle.journal import PostgresStockoutJournal
from retailops_ai.stockout_lifecycle.publish import publish_approval
from retailops_ai.stockout_lifecycle.qualification import approve_stockout
from retailops_ai.stockout_lifecycle.registry import MLflowStockoutRegistry
from retailops_ai.stockout_lifecycle.release import (
    predict_smoke,
    receipt,
    signature,
    verify_approved_capsule,
)
from retailops_ai.stockout_runtime.inputs import PreparedStockoutInputs
from retailops_ai.stockout_selection.contract import ConditionalRiskPipeline


def require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError(reason)


def private_json(path: Path, document: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(document) + b"\n")
    path.chmod(0o600)


def review(
    root: Path, image: str, work: Path, proof: dict[str, Any]
) -> tuple[StockoutQualification, ApprovalRequest]:
    q = StockoutQualification.model_validate_json(read_bytes(root, "qualification.json"))
    require(
        q.purpose == "qualified_stockout"
        and q.quality_status == "passed_independent_final_campaign",
        "final_acceptance_real_quality_required",
    )
    if not isinstance(q.recipe.pipeline, ConditionalRiskPipeline):
        raise ValueError("final_acceptance_frozen_conditional_model_required")
    freeze = CampaignFreeze.model_validate_json(read_bytes(root, "campaign_freeze.json"))
    permission = CampaignPermission.model_validate_json(
        read_bytes(root, "campaign_permission.json")
    )
    quality = read_json(root, "final_quality.json")
    execution = read_json(root, "execution_evidence.json")
    inputs = PreparedStockoutInputs.model_validate_json(read_bytes(root, "inputs.json"))
    card = read_json(root, "model_card.json")
    selection = read_json(root, "selection.json")
    for world in quality["content"]["worlds"]:
        verify_world_gates(world, freeze)
    require(
        quality == aggregate(quality["content"]["worlds"], freeze=freeze, permission=permission),
        "final_acceptance_quality_replay_changed",
    )
    verify_execution_evidence(execution, quality, freeze=freeze, permission=permission)
    require(
        card
        == final_card(
            selection=selection,
            freeze=freeze,
            permission=permission,
            recipe=q.recipe,
            policy=q.policy,
            quality=quality,
            execution=execution,
            inputs=inputs,
        ),
        "final_acceptance_card_replay_changed",
    )
    smoke = read_json(root, "smoke.json")
    reproduced = predict_smoke(
        inputs, q.recipe, q.policy, generated_at=datetime.fromisoformat(smoke["generated_at"])
    )
    require(smoke == reproduced.model_dump(mode="json"), "final_acceptance_smoke_changed")
    comparison = read_json(ROOT / "docs/evidence", "08-24-capacity-comparison.json")
    checks: dict[str, dict[str, bool]] = {
        "source": {
            "six_verified_sources": len(freeze.sources) == 6,
            "independent_complete_world_replay": quality["content"]["status"] == "passed",
        },
        "features": {
            "complete_public_parent_replay": inputs.parent_replay
            == "complete_public_features_and_upstream",
            "input_receipt": receipt(read_bytes(root, "inputs.json")) == q.public_inputs,
        },
        "pit": {
            "roles_disjoint": selection["selection"]["content"]["roles_disjoint"] is True,
            "chronology": q.recipe.pipeline.base.fit_known_at
            < q.recipe.pipeline.calibrator.fit_known_at
            < q.recipe.pin.selection_known_at
            <= inputs.as_of,
        },
        "protocol": {
            "code_pin": freeze.evaluator_code_sha256 == code_digest(),
            "lock_pin": freeze.dependency_lock_sha256 == lock_digest(),
            "no_final_refit": all(
                w["model_refits"] == 0 and w["recalibration"] is False
                for w in quality["content"]["worlds"]
            ),
        },
        "segments": {
            "six_separate_worlds": len(quality["content"]["worlds"]) == 6,
            "no_blockers": not quality["content"]["blockers"],
        },
        "calibration": {
            "all_equations_replayed": quality["content"]["status"] == "passed",
            "warnings_visible_and_preapproved": card["warnings"] == quality["content"]["warnings"]
            and permission.small_category_warnings_approved,
        },
        "threshold_capacity": {
            "development_only_choice": comparison["content"]["final_test_outcomes_evaluated"]
            is False,
            "frozen_chosen_policy": comparison["scoring_policy"]
            == q.policy.model_dump(mode="json"),
            "owner_permission": permission.thresholds_and_capacity_approved,
        },
        "signature": {
            "schema_replay": read_json(root, "signature.json") == signature(q.recipe, q.policy),
            "signature_receipt": receipt(read_bytes(root, "signature.json")) == q.signature,
        },
        "resources": {
            "all_remote_measurements_passed": all(
                w["resource"]["status"] == "passed" for w in execution["content"]["worlds"]
            ),
            "bounded_smoke": 1 <= len(inputs.points) <= 100,
        },
        "security_license": {
            "focused_tests_passed": proof["tests"] >= 190
            and proof["failures"] == 0
            and proof["skipped"] == 0,
            "secret_scan_completed": proof["secret_scan_passed"] is True,
            "MIT_project_license": (ROOT / "LICENSE").read_text().startswith("MIT License"),
            "no_dependency_change": lock_digest() == freeze.dependency_lock_sha256,
        },
        "model_card": {
            "full_card_replayed": True,
            "card_receipt": receipt(read_bytes(root, "model_card.json")) == q.model_card,
        },
        "freshness_drift_compatibility": {
            "smoke_fully_replayed": True,
            "freshness_explicit": inputs.lineage.source_watermark is None
            and inputs.lineage.source_completeness_status == "unavailable",
            "stress_each_seed": len([s for s in freeze.sources if s.world == "future_stress"]) == 3,
        },
    }
    require(set(checks) == REVIEW_GATES, "final_acceptance_review_inventory")
    gates = {}
    for name, equations in checks.items():
        require(all(equations.values()), "final_acceptance_gate_failed_" + name)
        report = dict(
            qualification_id=q.qualification_id,
            gate=name,
            status="passed",
            purpose="actual_qualified_model_review_for_isolated_runner",
            checks=equations,
            final_quality_id=quality["quality_id"],
            execution_evidence_id=execution["evidence_id"],
            image_digest=image,
            run_id=os.environ["GITHUB_RUN_ID"],
            commit=os.environ["GITHUB_SHA"],
            tests=proof,
            limitations=card["limitations"],
            warnings=card["warnings"],
        )
        path = work / "reports" / (name + ".json")
        private_json(path, report)
        gates[name] = dict(
            status="passed", report=receipt(path.read_bytes()).model_dump(mode="json")
        )
    return q, ApprovalRequest.model_validate_json(
        canonical_bytes(
            dict(
                qualification_id=q.qualification_id,
                image_digest=image,
                gates=gates,
                reason="Owner-approved AI08 completion: actual frozen model on an isolated disposable acceptance runner.",
            )
        )
    )


def access(work: Path, q: StockoutQualification) -> tuple[Path, dict[str, str], Principal]:
    now = datetime.now(UTC)
    tokens = {
        p: secrets.token_urlsafe(32)
        for p in (
            "ai08-acceptance-promoter",
            "ai08-acceptance-pipeline",
            "ai08-acceptance-outsider",
        )
    }
    scope = dict(
        product_ids=list(q.smoke_scope.product_ids),
        stock_location_ids=list(q.smoke_scope.stock_location_ids),
    )
    grants = [
        dict(
            principal_id="ai08-acceptance-promoter",
            roles=["promoter"],
            capabilities=["model:decide"],
            scope=None,
        ),
        dict(
            principal_id="ai08-acceptance-pipeline",
            roles=["pipeline"],
            capabilities=["stockout:read", "stockout:run"],
            scope=None,
            stockout_scope=scope,
        ),
        dict(
            principal_id="ai08-acceptance-outsider",
            roles=["viewer"],
            capabilities=["stockout:read"],
            scope=None,
            stockout_scope=dict(
                product_ids=["outside"], stock_location_ids=scope["stock_location_ids"]
            ),
        ),
    ]
    policy = AccessPolicy.model_validate_json(
        canonical_bytes(
            dict(
                schema_version="1.0",
                policy_id="ai08-isolated-acceptance",
                grants=grants,
                credentials=[
                    dict(
                        principal_id=p,
                        token_sha256=token_fingerprint(t),
                        not_before=(now - timedelta(seconds=1)).isoformat(),
                        expires_at=(now + timedelta(hours=1)).isoformat(),
                        revoked=False,
                    )
                    for p, t in tokens.items()
                ],
            )
        )
    )
    path = work / "policy.json"
    private_json(path, policy.model_dump(mode="json"))
    promoter = LocalAccess(load_private_policy(path)).authenticate(
        "Bearer " + tokens["ai08-acceptance-promoter"]
    )
    require(promoter is not None, "final_acceptance_authentication_failed")
    if promoter is None:
        raise ValueError("final_acceptance_authentication_failed")
    return path, tokens, promoter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--mlflow-image", required=True)
    parser.add_argument("--tests", type=Path, required=True)
    parser.add_argument("--secret-scan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(
        sys.platform == "linux"
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_OS") == "Linux"
        and os.environ.get("GITHUB_REPOSITORY") == "Oskar-Stachowski/retailops-ai-intelligence",
        "final_acceptance_owned_runner_required",
    )
    require(
        shutil.disk_usage(args.output.parent).free >= 6 * 1024**3, "final_acceptance_remote_reserve"
    )
    image = docker("image", "inspect", "--format", "{{.Id}}", args.image)
    image_pins = json.loads(
        docker(
            "run",
            "--rm",
            "--pull=never",
            "--memory=256m",
            "--cpus=1",
            "--network=none",
            "--entrypoint",
            "python",
            image,
            "-c",
            "import json;from retailops_ai.stockout_campaign.implementation import code_digest,lock_digest;print(json.dumps(dict(code=code_digest(),lock=lock_digest())))",
        )
    )
    require(
        image_pins == dict(code=code_digest(), lock=lock_digest()),
        "final_acceptance_installed_image_code_or_lock",
    )
    # This file is produced by the preceding fixed pytest command on the same runner.
    suites = xml.parse(args.tests).getroot().findall("testsuite")  # noqa: S314
    secret_scan = json.loads(args.secret_scan.read_bytes())
    require(
        secret_scan["workflow_run_id"] == os.environ["GITHUB_RUN_ID"]
        and secret_scan["commit"] == os.environ["GITHUB_SHA"]
        and secret_scan["scanner"] == "gitleaks-8.30.1",
        "final_acceptance_secret_scan_execution_mismatch",
    )
    proof = dict(
        tests=sum(int(s.get("tests", "0")) for s in suites),
        failures=sum(int(s.get("failures", "0")) + int(s.get("errors", "0")) for s in suites),
        skipped=sum(int(s.get("skipped", "0")) for s in suites),
        secret_scan_passed=secret_scan["passed"],
        tests_sha256=receipt(args.tests.read_bytes()).sha256,
    )
    started = time.perf_counter()
    args.output.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix="ai08-final-acceptance-") as temp:
        work = Path(temp)
        q, reviewed = review(args.qualification, image, work / "review", proof)
        policy_file, tokens, promoter = access(work, q)
        capsule = approve_stockout(
            args.qualification,
            actor=promoter,
            request=reviewed,
            reports=work / "review",
            output=work / "approved",
        )
        approval = verify_approved_capsule(capsule, approval_id=capsule.name)
        images = {
            "db": docker("image", "inspect", "--format", "{{.Id}}", IMAGES["db"]),
            "mlflow": docker("image", "inspect", "--format", "{{.Id}}", args.mlflow_image),
        }
        (work / "stack").mkdir(mode=0o700)
        fixture = FixtureStack.create(work / "stack", uuid.uuid4().hex, images)
        project = fixture.projects["source"]
        engine = None
        try:
            store.checked_run(
                fixture.compose(
                    project, "up", "--no-build", "--pull", "never", "-d", "--wait", "db"
                )
            )
            url = fixture.url(project)
            migrate(Settings(APP_ENV="test", ARTIFACT_ROOT=work / "artifacts", DATABASE_URL=url))
            store.checked_run(
                fixture.compose(
                    project, "up", "--no-build", "--pull", "never", "-d", "--wait", "mlflow"
                )
            )
            wait_ready(url, fixture.mlflow_port(project))
            engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 3})
            registry = MLflowStockoutRegistry(environment="test", port=fixture.mlflow_port(project))
            imported = publish_approval(
                capsule,
                registry,
                approval_id=approval.release_id,
                model=MODEL,
                actor=promoter,
                work=work / "imports",
            )
            repeated = publish_approval(
                capsule,
                registry,
                approval_id=approval.release_id,
                model=MODEL,
                actor=promoter,
                work=work / "imports",
            )
            require(
                repeated["status"] == "already_imported"
                and repeated["mlflow_run_id"] == imported["mlflow_run_id"],
                "final_acceptance_import_not_idempotent",
            )
            journal = PostgresStockoutJournal(engine)
            lifecycle = StockoutLifecycle(registry, journal, environment="test")

            def decide(
                action: Literal["register", "reject", "promote", "rollback"],
                name: str,
                version: str | None = None,
            ) -> dict[str, Any]:
                request = StockoutLifecycleRequest(
                    decision_id="decision-ai08-final-" + name,
                    action=action,
                    model_name="retailops-stockout-risk",
                    mlflow_run_id=imported["mlflow_run_id"] if action == "register" else None,
                    model_version=version,
                    approval_id=approval.release_id,
                    approval_sha256=imported["approval_sha256"],
                    image_digest=image if action == "promote" else None,
                    reason="Explicit isolated AI08 acceptance decision under owner-approved stage completion.",
                )
                return lifecycle.execute(request, promoter)

            v1 = decide("register", "register-one")["model_version"]
            decide("promote", "promote-one", v1)
            v2 = decide("register", "register-two")["model_version"]
            decide("promote", "promote-two", v2)
            rolled = decide("rollback", "rollback-one", v1)
            v3 = decide("register", "register-rejected")["model_version"]
            decide("reject", "reject-one", v3)
            with journal.locked(MODEL):
                release = journal.active(MODEL)
                require(
                    release is not None and release.release_id == rolled["release_id"],
                    "final_acceptance_rollback_head",
                )
            if release is None:
                raise ValueError("final_acceptance_rollback_head")
            registry.validate(release.binding)
            inputs = PreparedStockoutInputs.model_validate_json(read_bytes(capsule, "inputs.json"))
            # These exact bytes were built by the public full-parent qualifier, not test fixtures.
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ai.stockout_prepared_inputs(environment,inputs_id,registered_by,inputs) VALUES ('test',:id,:principal,CAST(:inputs AS jsonb))"
                    ),
                    dict(
                        id=inputs.inputs_id,
                        principal="ai08-acceptance-pipeline",
                        inputs=inputs.model_dump_json(),
                    ),
                )
            queue = PostgresStockoutQueue(engine, "test")
            reader = PostgresStockoutReader(engine, "test")
            app = create_app(
                Settings(
                    APP_ENV="test",
                    ARTIFACT_ROOT=work / "artifacts",
                    DATABASE_URL=url,
                    API_AUTH_FILE=policy_file,
                ),
                stockout_administration=PostgresStockoutAdministration(queue),
                stockout_reader=reader,
            )
            body = StockoutRequest(
                profile_id=inputs.inputs_id, as_of=inputs.as_of, scope=inputs.scope
            )
            with TestClient(app, base_url="http://127.0.0.1") as client:
                require(
                    client.get("/api/v1/stockout-risks").status_code == 401,
                    "final_acceptance_unauthenticated_read",
                )
                headers = {
                    "Authorization": "Bearer " + tokens["ai08-acceptance-pipeline"],
                    "Idempotency-Key": "ai08-final-public-model",
                }
                response = client.post(
                    "/api/v1/stockout-runs", json=body.model_dump(mode="json"), headers=headers
                )
                require(response.status_code == 202, "final_acceptance_HTTP_submission")
                run_id = response.json()["run_id"]
                require(
                    client.post(
                        "/api/v1/stockout-runs", json=body.model_dump(mode="json"), headers=headers
                    ).json()["run_id"]
                    == run_id,
                    "final_acceptance_HTTP_idempotency",
                )
                preload(engine, registry, queue, release_id=release.release_id, image_digest=image)
                claim = queue.claim(release_id=release.release_id)
                require(
                    claim is not None and claim.run.run_id == run_id,
                    "final_acceptance_claim_required",
                )
                if claim is None:
                    raise ValueError("final_acceptance_claim_required")
                require(
                    run_attempt(
                        queue,
                        claim,
                        image_digest=image,
                        guard=lambda full: registry_guard(engine, registry, release, full=full),
                    )
                    == "succeeded",
                    "final_acceptance_real_worker_failed",
                )
                response = client.get(
                    "/api/v1/stockout-risks",
                    params=dict(inference_run_id=run_id, view="attention_queue"),
                    headers=headers,
                )
                require(
                    response.status_code == 200 and bool(response.json()["items"]),
                    "final_acceptance_attention_queue",
                )
                examples = response.json()
                require(
                    all(r["quality_status"] == "passed_at_publication" for r in examples["items"]),
                    "final_acceptance_qualified_output_required",
                )
                require(
                    client.get("/api/v1/stockout-runs/" + run_id, headers=headers).json()["status"]
                    == "succeeded",
                    "final_acceptance_durable_run",
                )
                require(
                    client.get(
                        "/api/v1/stockout-risks/" + examples["items"][0]["risk_id"],
                        headers={"Authorization": "Bearer " + tokens["ai08-acceptance-outsider"]},
                    ).status_code
                    == 404,
                    "final_acceptance_foreign_scope",
                )
            pipeline = LocalAccess(load_private_policy(policy_file)).authenticate(
                "Bearer " + tokens["ai08-acceptance-pipeline"]
            )
            if pipeline is None:
                raise ValueError("final_acceptance_pipeline_auth")
            output = queue.output(run_id, pipeline)
            require(
                len(output.items) == q.smoke_rows
                and all(r.model_name == MODEL for r in output.items),
                "final_acceptance_complete_batch",
            )
            state = dict(
                status="passed",
                purpose="actual_final_model_on_isolated_disposable_runner",
                campaign_id=q.final_campaign_id,
                qualification_id=q.qualification_id,
                approval_id=approval.release_id,
                image_digest=image,
                mlflow_run_id=imported["mlflow_run_id"],
                registered_versions=[v1, v2, v3],
                promoted_versions=[v1, v2],
                rolled_back_to=v1,
                rejected_version=v3,
                batch_run_id=run_id,
                output_id=output.output_id,
                smoke_rows=q.smoke_rows,
                real_public_inputs=True,
                real_mlflow=True,
                real_postgres=True,
                real_cold_worker=True,
                actual_HTTP_auth_scope_idempotency=True,
                review_gates=sorted(REVIEW_GATES),
                model_refits=0,
                source_generation=False,
                production_deployed=False,
                commit=os.environ["GITHUB_SHA"],
                workflow_run_id=int(os.environ["GITHUB_RUN_ID"]),
                wall_seconds=time.perf_counter() - started,
            )
            private_json(args.output / "acceptance.json", state)
            private_json(args.output / "api-attention-example.json", examples)
            private_json(args.output / "approval.json", approval.model_dump(mode="json"))
            shutil.copytree(capsule / "reports", args.output / "reports")
        finally:
            if engine is not None:
                engine.dispose()
            fixture.cleanup(project)
    print(
        json.dumps(
            dict(
                status="passed",
                qualification_id=q.qualification_id,
                real_registry_lifecycle_batch_HTTP=True,
                production_deployed=False,
            )
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            json.dumps(
                dict(
                    error="stockout_final_acceptance_failed",
                    kind=type(error).__name__,
                    reason=str(error)
                    if isinstance(error, ValueError) and str(error).startswith("final_acceptance_")
                    else "internal_failure",
                )
            )
        )
        raise SystemExit(1) from None
