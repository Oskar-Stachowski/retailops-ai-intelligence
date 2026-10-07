"""Original frozen v12 -> reviewed development registry -> real queue, SQL and native outbox."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as xml
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from check_stockout_final_acceptance import private_json, require
from check_v12_lifecycle import IMAGES, ROOT, docker, port, wait_ready
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.curated.builder import verify_curated
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.contracts import BatchRequest
from retailops_ai.forecast_jobs.input_store import PostgresInputStore
from retailops_ai.forecast_jobs.inputs import PreparedInputs, verify_inputs_package
from retailops_ai.forecast_jobs.read_contracts import ForecastQuery
from retailops_ai.forecast_jobs.v12_administration import PostgresV12JobAdministration
from retailops_ai.forecast_jobs.v12_inference_contracts import V12InferenceResult
from retailops_ai.forecast_jobs.v12_output_store import PostgresV12Publisher
from retailops_ai.forecast_jobs.v12_queue import PostgresV12Queue
from retailops_ai.forecast_jobs.v12_reader import PostgresV12ForecastReader
from retailops_ai.forecast_jobs.v12_worker import preload, registry_guard, run_attempt
from retailops_ai.forecasting.manifests import verify_feature_set
from retailops_ai.intelligence_events.contracts import ForecastGenerated, forecast_events
from retailops_ai.migrations.runner import migrate
from retailops_ai.model_lifecycle.contracts import GATES
from retailops_ai.model_lifecycle.v12_development import read_development_acceptance
from retailops_ai.model_lifecycle.v12_evidence import load_evidence
from retailops_ai.model_lifecycle.v12_journal import PostgresV12Journal
from retailops_ai.model_lifecycle.v12_lifecycle import V12Lifecycle
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    DEVELOPMENT_MODEL,
    V12LifecycleRequest,
)
from retailops_ai.model_lifecycle.v12_mlflow import import_evidence
from retailops_ai.model_lifecycle.v12_registry import MLflowV12Registry, publish_approval
from retailops_ai.model_lifecycle.v12_release import (
    approve_v12,
    load_approved_v12,
    qualify_v12,
    receipt,
)
from retailops_ai.model_lifecycle.v12_release_contracts import V12ApprovalRequest, V12Qualification
from retailops_ai.model_lifecycle.v12_scratch_tracking import ScratchTracking
from retailops_ai.security.local import LocalAccess, load_private_policy, token_fingerprint
from retailops_ai.security.models import AccessPolicy
from retailops_ai.source_snapshot.files import file_hash, read_bytes, read_json


def original_database_url(password: str, port_number: int) -> str:
    return f"postgresql+psycopg://ai_app:{password}@127.0.0.1:{port_number}/retailops_ai"


def application_settings(work: Path, url: str, policy: Path | None) -> Settings:
    settings = Settings(
        APP_ENV="test",
        ARTIFACT_ROOT=work / "artifacts",
        DATABASE_URL=url,
        API_AUTH_FILE=policy,
        V12_DEVELOPMENT_MODE=True,
    )
    # Exercise the real application constructor, including its independent index
    # database boundary. Construction does not connect or start a service.
    create_app(settings)
    return settings


def preflight_application_database() -> None:
    with tempfile.TemporaryDirectory(prefix="ai10-v12-config-") as temporary:
        application_settings(Path(temporary), original_database_url(secrets.token_hex(24), 1), None)
    print('{"stage":"actual_application_database_configuration","status":"passed"}')


def access(work: Path, inputs: PreparedInputs) -> tuple[Path, dict[str, str], Principal, Principal]:
    now = datetime.now(UTC)
    tokens = {name: secrets.token_urlsafe(32) for name in ("promoter", "pipeline", "outsider")}
    scope = dict(
        product_ids=list(inputs.scope.product_ids),
        selling_location_ids=list(inputs.scope.selling_location_ids),
        channels=[inputs.scope.channel],
    )
    policy = AccessPolicy.model_validate_json(
        canonical_bytes(
            dict(
                schema_version="1.0",
                policy_id="ai10-v12-owned-acceptance",
                grants=[
                    dict(
                        principal_id="promoter",
                        roles=["promoter"],
                        capabilities=["model:decide"],
                        scope=None,
                    ),
                    dict(
                        principal_id="pipeline",
                        roles=["pipeline"],
                        capabilities=["forecast:run", "forecast:read"],
                        scope=scope,
                    ),
                    dict(
                        principal_id="outsider",
                        roles=["viewer"],
                        capabilities=["forecast:read"],
                        scope={**scope, "product_ids": ["outside"]},
                    ),
                ],
                credentials=[
                    dict(
                        principal_id=name,
                        token_sha256=token_fingerprint(token),
                        not_before=(now - timedelta(seconds=1)).isoformat(),
                        expires_at=(now + timedelta(hours=4)).isoformat(),
                        revoked=False,
                    )
                    for name, token in tokens.items()
                ],
            )
        )
    )
    path = work / "access.json"
    private_json(path, policy.model_dump(mode="json"))
    authority = LocalAccess(load_private_policy(path))
    promoter = authority.authenticate("Bearer " + tokens["promoter"])
    pipeline = authority.authenticate("Bearer " + tokens["pipeline"])
    require(promoter is not None and pipeline is not None, "ai10_v12_private_authentication")
    if promoter is None or pipeline is None:
        raise ValueError("ai10_v12_private_authentication")
    return path, tokens, promoter, pipeline


def reviews(
    qualification_dir: Path,
    run: Path,
    parents: dict[str, Any],
    pins: dict[str, Any],
    image: str,
    tests: Path,
    scan: Path,
    output: Path,
) -> V12ApprovalRequest:
    q = V12Qualification.model_validate_json(read_bytes(qualification_dir, "qualification.json"))
    smoke = V12InferenceResult.model_validate_json(read_bytes(qualification_dir, "smoke.json"))
    inputs = verify_inputs_package(Path(parents["inputs_dir"]))
    curated = verify_curated(Path(parents["curated_dir"]))
    features = verify_feature_set(Path(parents["feature_dir"]))
    scan_result = read_json(scan.parent, scan.name)
    suites = xml.parse(tests).getroot().findall("testsuite")  # noqa: S314 - own preceding pytest output
    test_counts = {
        name: sum(int(s.get(name, "0")) for s in suites)
        for name in ("tests", "failures", "errors", "skipped")
    }
    source = read_json(Path(parents["snapshot_dir"]), "snapshot_manifest.json")
    owner = q.development_acceptance
    checks: dict[str, dict[str, bool]] = {
        "source": {
            "whole_43_table_source_verified": len(source["tables"]) == 43
            and q.source_packages_verified,
            "separate_verified_inference_snapshot": q.source_policy.mode
            == "verified_inference_snapshot"
            and q.source_policy.source_dataset_id == source["source_dataset_id"],
            "truth_not_imported": source["descriptor"]["include_evaluation_truth"] is False,
        },
        "features": {
            "verified_curated_and_feature_parents": features == inputs.feature_manifest
            and curated["readiness"]["forecast_source"] == "passed",
            "complete_scope_and_planned_future_calendar": len(inputs.rows) == 56
            and len(inputs.histories) == 4
            and parents["history_days"] == 102
            and parents["forecast_plan_days"] == 14,
        },
        "pit": {
            "origin_after_frozen_selection": inputs.as_of_time > q.pin.fold.selection_cutoff,
            "all_feature_references_known_at_origin": all(
                value.source_available_at is None
                or value.source_available_at <= row.forecast_origin
                for row in inputs.rows
                for value in row.values
            ),
            "typed_label_free_inference_contract": inputs.schema_version == "1.1"
            and smoke.inference.purpose == "serving_load_predict_acceptance",
        },
        "protocol": {
            "original_whole_export_verified": q.full_export_verified
            and q.pin.manifest.sha256 == pins["run_manifest_sha256"],
            "exact_original_recipe_no_metric_selection": q.pin.recipe_id == pins["recipe_id"]
            and q.pin.recipe.sha256 == pins["recipe_sha256"],
            "unchanged_original_quality_no_refit": q.pin.forecast_model_status == "not_ready"
            and smoke.model_refits == 0
            and smoke.source_generation is False,
        },
        "segments": {
            "exact_owner_development_acceptance": owner is not None
            and owner.decision.sha256 == pins["owner_acceptance_sha256"],
            "no_quality_reclassification_or_production": owner is not None
            and owner.original_quality_reclassified is False
            and owner.production_deployment_authorized is False,
            "separate_development_namespace": pins["model_namespace"] == DEVELOPMENT_MODEL,
        },
        "signature": {
            "unchanged_full_original_signature": file_hash(run, "signature.json")
            == (q.pin.signature.size_bytes, q.pin.signature.sha256),
            "actual_frozen_predictor_repeatability": q.repeatability_verified
            and smoke.profile_id == inputs.profile_id
            and len(smoke.predictions) == 56,
        },
        "resources": {
            "measured_cold_process_within_limits": smoke.cold_load_seconds + smoke.compute_seconds
            <= q.limits.wall_seconds
            and smoke.peak_rss_bytes <= q.limits.rss_bytes,
            "hosted_reserve_retained": shutil.disk_usage(run).free >= 6 * 1024**3,
        },
        "security_license": {
            "complete_history_scan_same_execution": scan_result["passed"] is True
            and scan_result["commit"] == os.environ["GITHUB_SHA"]
            and scan_result["workflow_run_id"] == os.environ["GITHUB_RUN_ID"]
            and scan_result["scanner"] == "gitleaks-8.30.1",
            "boundary_tests_no_failures_or_skips": test_counts["tests"] >= 100
            and test_counts["failures"] == test_counts["errors"] == test_counts["skipped"] == 0,
            "MIT_license": (ROOT / "LICENSE").read_text().startswith("MIT License"),
            "original_dependency_lock": inputs.feature_manifest.descriptor.code.dependency_lock_sha256
            == q.pin.dependency_lock_sha256,
        },
        "model_card": {
            "original_card_and_recipe_full_verification": q.full_export_verified
            and read_json(run, "model_card.json")["recipes"][pins["cohort_id"]][pins["fold"]][
                "artifact"
            ]["recipe_id"]
            == q.pin.recipe_id,
            "development_limit_visible": q.development_acceptance is not None
            and pins["original_quality_status"] == "not_ready",
        },
        "freshness_drift_compatibility": {
            "actual_source_watermark_supported": inputs.source_freshness is not None
            and inputs.source_freshness.watermark is not None
            and inputs.source_freshness.watermark.supported,
            "complete_source_through_origin": inputs.source_freshness is not None
            and inputs.source_freshness.watermark is not None
            and inputs.source_freshness.watermark.complete_through == inputs.as_of_time.date(),
            "current_origin": 0 <= (datetime.now(UTC) - inputs.as_of_time).total_seconds() <= 86400,
            "inference_drift_not_scientific_qualification": q.source_policy.mode
            == "verified_inference_snapshot"
            and pins["original_quality_reclassified"] is False,
        },
    }
    require(set(checks) == GATES, "ai10_v12_review_inventory")
    gates = {}
    for gate, equations in checks.items():
        require(all(equations.values()), "ai10_v12_review_failed_" + gate)
        path = output / "reports" / (gate + ".json")
        private_json(
            path,
            dict(
                qualification_id=q.qualification_id,
                gate=gate,
                status="passed",
                scope="original_v12_owner_accepted_development_only",
                checks=equations,
                original_quality_status="not_ready",
                original_quality_reclassified=False,
                model_refits=0,
                production_deployed=False,
                image_digest=image,
                commit=os.environ["GITHUB_SHA"],
                workflow_run_id=int(os.environ["GITHUB_RUN_ID"]),
            ),
        )
        gates[gate] = dict(
            status="passed", report=receipt(path.read_bytes()).model_dump(mode="json")
        )
    return V12ApprovalRequest.model_validate_json(
        canonical_bytes(
            dict(
                qualification_id=q.qualification_id,
                image_digest=image,
                gates=gates,
                reason="Owner-accepted original v12 development use on separately verified AI10 inference parents; unchanged original quality.",
            )
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("original", "parents", "output", "tests", "secret-scan"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--consumer-root", type=Path, required=True)
    args = parser.parse_args()
    owner = uuid.uuid4().hex
    names = {role: "retailops-ai10-v12-" + role + "-" + owner[:12] for role in IMAGES}
    owned: list[str] = []
    created_output = False
    engine = None
    stage = "owned_runner_guard"
    report: dict[str, Any] = dict(
        status="running",
        stages=[],
        original_quality_status="not_ready",
        original_quality_reclassified=False,
        model_refits=0,
        production_deployed=False,
    )

    def passed(name: str) -> None:
        report["stages"].append(name)
        print(json.dumps(dict(stage=name, status="passed")), flush=True)

    try:
        require(
            sys.platform == "linux"
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
            and os.environ.get("GITHUB_REPOSITORY") == "Oskar-Stachowski/retailops-ai-intelligence",
            "ai10_v12_owned_hosted_runner_required",
        )
        args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
        created_output = True
        work = args.output.parent / ("v12-private-" + owner)
        work.mkdir(mode=0o700)
        pins = read_json(ROOT / "docs/reference", "ai10-v12-serving-request.json")
        parents = read_json(args.parents.parent, args.parents.name)
        require(
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],  # noqa: S607 - fixed read-only command on pinned checkout
                cwd=args.consumer_root,
                text=True,
            ).strip()
            == pins["source_commit"],
            "ai10_v12_exact_source_consumer_commit",
        )
        original = args.original.resolve()
        run = original / "archive" / pins["run_id"]
        python = original / "support/frozen-runtime/.venv/bin/python"
        image = docker("image", "inspect", "--format", "{{.Id}}", args.image)
        require(
            re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None, "ai10_v12_actual_image_digest"
        )
        # Actual installed current image agrees with the host boundary code; the model
        # itself executes in the separate, exact original AI04 wheel and never refits.
        modules = [
            "forecast_jobs/v12_worker.py",
            "forecast_jobs/v12_output_store.py",
            "intelligence_events/contracts.py",
            "model_lifecycle/v12_registry.py",
            "model_lifecycle/v12_development.py",
        ]
        code = (
            "import hashlib,json,retailops_ai;from pathlib import Path;r=Path(retailops_ai.__file__).parent;names="
            + repr(modules)
            + ";print(json.dumps({n:hashlib.sha256((r/n).read_bytes()).hexdigest() for n in names}))"
        )
        installed = json.loads(
            docker(
                "run",
                "--rm",
                "--pull=never",
                "--network=none",
                "--memory=256m",
                "--cpus=1",
                "--entrypoint",
                "python",
                image,
                "-c",
                code,
            )
        )
        require(
            installed
            == {
                name: hashlib.sha256((ROOT / "src/retailops_ai" / name).read_bytes()).hexdigest()
                for name in modules
            },
            "ai10_v12_actual_image_code_binding",
        )
        acceptance = read_development_acceptance(original / "support/04-v12-acceptance.json")
        require(
            acceptance.decision.sha256 == pins["owner_acceptance_sha256"],
            "ai10_v12_original_owner_acceptance",
        )
        stage = "full_original_qualification_and_two_frozen_cold_predictions"
        qualification = qualify_v12(
            run,
            python,
            run_id=pins["run_id"],
            cohort_id=pins["cohort_id"],
            fold=pins["fold"],
            recipe_id=pins["recipe_id"],
            inputs_dir=Path(parents["inputs_dir"]),
            feature_dir=Path(parents["feature_dir"]),
            curated_dir=Path(parents["curated_dir"]),
            output_root=work / "qualifications",
            valid_until=datetime.now(UTC) + timedelta(hours=24),
            development_acceptance=acceptance,
            allow_new_source=True,
        )
        passed(stage)
        stage = "ten_actual_review_gates_and_full_original_approval"
        request = reviews(
            qualification, run, parents, pins, image, args.tests, args.secret_scan, work / "review"
        )
        inputs = verify_inputs_package(Path(parents["inputs_dir"]))
        policy, tokens, promoter, pipeline = access(work, inputs)
        approval = approve_v12(
            qualification,
            run,
            python,
            actor=promoter,
            request=request,
            reports_dir=work / "review",
            output_root=work / "approvals",
        )
        loaded = load_approved_v12(
            approval, run, python, release_id=approval.name, image_digest=image
        )
        shutil.copytree(approval, args.output / "approved-release" / approval.name)
        (args.output / "frozen-recipe.json").write_bytes(read_bytes(run, pins["recipe_path"]))
        (args.output / "frozen-recipe.json").chmod(0o600)
        passed(stage)
        stage = "real_owned_pg16_mlflow_and_full_original_http_import"
        store = work / "mlflow-artifacts"
        store.mkdir(mode=0o700)
        password = secrets.token_hex(24)
        for role in names:
            require(
                not docker("inspect", names[role], required=False),
                "ai10_v12_fresh_container_required",
            )
        docker(
            "run",
            "-d",
            "--pull=never",
            "--name",
            names["db"],
            "--label",
            "retailops.ai10.v12.owner=" + owner,
            "--memory=512m",
            "--cpus=1",
            "-p",
            "127.0.0.1::5432",
            "-e",
            "POSTGRES_USER=ai_app",
            "-e",
            "POSTGRES_PASSWORD=" + password,
            "-e",
            "POSTGRES_DB=retailops_ai",
            IMAGES["db"],
        )
        owned.append(names["db"])
        docker(
            "run",
            "-d",
            "--pull=never",
            "--name",
            names["mlflow"],
            "--label",
            "retailops.ai10.v12.owner=" + owner,
            "--user=0",
            "--memory=1g",
            "--cpus=1",
            "-p",
            "127.0.0.1::5000",
            "--mount",
            "type=bind,source=" + str(store.resolve()) + ",target=/var/mlflow/artifacts",
            IMAGES["mlflow"],
            "mlflow",
            "server",
            "--host",
            "0.0.0.0",  # noqa: S104 - owned container with loopback-only host mapping
            "--port",
            "5000",
            "--workers",
            "1",
            "--backend-store-uri",
            "sqlite:////tmp/ai10-v12.sqlite",
            "--artifacts-destination",
            "/var/mlflow/artifacts",
        )  # noqa: S104,S108 - owned isolated container only
        owned.append(names["mlflow"])
        mlflow_port = port(names["mlflow"], "5000/tcp")
        url = original_database_url(password, port(names["db"], "5432/tcp"))
        wait_ready(url, mlflow_port)
        engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 3})
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA ai"))
            connection.execute(text("CREATE EXTENSION vector"))
        settings = application_settings(work, url, policy)
        migrate(settings)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ai.service_metadata(name,value) VALUES ('ai10_v12_native_owner',CAST(:owner AS jsonb))"
                ),
                dict(owner=json.dumps(owner)),
            )
        evidence = load_evidence(run, python)
        campaign = import_evidence(
            evidence, ScratchTracking(store, run, mlflow_port), work / "campaign-import"
        )
        require(
            campaign["artifact_files"] == 664 and campaign["quality_status"] == "not_ready",
            "ai10_v12_complete_original_mlflow_import",
        )
        passed(stage)
        stage = "real_development_registration_promotion_and_preload_before_intake"
        registry = MLflowV12Registry(environment="test", port=mlflow_port)
        imported = publish_approval(
            approval,
            loaded,
            registry,
            campaign_mlflow_run_id=campaign["mlflow_run_id"],
            model=DEVELOPMENT_MODEL,
            actor=promoter,
            work=work / "approval-import",
        )
        journal = PostgresV12Journal(engine)
        lifecycle = V12Lifecycle(registry, journal, environment="test")

        def decision(
            action: Literal["register", "promote"], version: str | None = None
        ) -> dict[str, Any]:
            return lifecycle.execute(
                V12LifecycleRequest(
                    decision_id="decision-ai10-v12-" + action + "-" + owner[:12],
                    action=action,
                    model_name="retailops-demand-forecast-v12-development",
                    mlflow_run_id=imported["mlflow_run_id"] if action == "register" else None,
                    model_version=version,
                    approval_id=loaded.release.release_id,
                    approval_sha256=imported["approval_sha256"],
                    image_digest=image if action == "promote" else None,
                    reason="Explicit original owner-accepted v12 development integration on owned disposable runner.",
                ),
                promoter,
            )

        version = decision("register")["model_version"]
        activated = decision("promote", version)
        queue = PostgresV12Queue(engine, "test", development=True)
        release, assets = preload(
            engine,
            registry,
            queue,
            release_id=activated["release_id"],
            approval_dir=approval,
            run_dir=run,
            verifier_python=python,
            image_digest=image,
        )
        PostgresInputStore(engine, "test").register(inputs)
        passed(stage)
        stage = "actual_authenticated_intake_cold_worker_atomic_publication_and_outbox"
        reader = PostgresV12ForecastReader(engine, "test", development=True)
        app = create_app(
            settings,
            v12_forecast_reader=reader,
            v12_forecast_administration=PostgresV12JobAdministration(queue),
        )
        with TestClient(app, base_url="http://127.0.0.1") as client:
            body = BatchRequest(
                profile_id=inputs.profile_id, as_of=inputs.as_of_time, channel=inputs.scope.channel
            )
            headers = {
                "Authorization": "Bearer " + tokens["pipeline"],
                "Idempotency-Key": "ai10-original-v12",
            }
            require(
                client.post(
                    "/api/v1/forecast-runs/v12", json=body.model_dump(mode="json")
                ).status_code
                == 401,
                "ai10_v12_anonymous_intake",
            )
            response = client.post(
                "/api/v1/forecast-runs/v12", headers=headers, json=body.model_dump(mode="json")
            )
            require(response.status_code == 202, "ai10_v12_actual_http_intake")
            run_id = response.json()["run_id"]
            require(
                client.post(
                    "/api/v1/forecast-runs/v12", headers=headers, json=body.model_dump(mode="json")
                ).json()["run_id"]
                == run_id,
                "ai10_v12_http_idempotency",
            )
            claim = queue.claim(release_id=release.release_id)
            require(claim is not None and claim.run.run_id == run_id, "ai10_v12_actual_claim")
            if claim is None:
                raise ValueError("ai10_v12_actual_claim")
            require(
                run_attempt(
                    queue,
                    claim,
                    assets,
                    guard=lambda full: registry_guard(engine, registry, release, full=full),
                )
                == "succeeded",
                "ai10_v12_real_frozen_worker",
            )
            publisher = PostgresV12Publisher(queue, registry, events_enabled=True)

            def fail_insert(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
                if statement.startswith("INSERT INTO ai.intelligence_outbox"):
                    raise RuntimeError("ai10_v12_controlled_outbox_insert_failure")

            event.listen(engine, "after_cursor_execute", fail_insert)
            try:
                try:
                    publisher.publish(run_id, pipeline)
                    raise ValueError("ai10_v12_outbox_failure_not_exercised")
                except RuntimeError as error:
                    require(
                        str(error) == "ai10_v12_controlled_outbox_insert_failure",
                        "ai10_v12_expected_failure",
                    )
            finally:
                event.remove(engine, "after_cursor_execute", fail_insert)
            with engine.connect() as connection:
                require(
                    connection.scalar(text("SELECT count(*) FROM ai.v12_forecast_outputs")) == 0
                    and connection.scalar(text("SELECT count(*) FROM ai.intelligence_outbox")) == 0,
                    "ai10_v12_atomic_rollback",
                )
            output = publisher.publish(run_id, pipeline)
            require(
                output == publisher.publish(run_id, pipeline), "ai10_v12_idempotent_publication"
            )
            job = client.get("/api/v1/forecast-runs/v12/" + run_id, headers=headers)
            require(
                job.status_code == 200
                and job.json()["output_ref"]["artifact_id"] == output.artifact_id,
                "ai10_v12_actual_public_status",
            )
            require(
                client.get(
                    "/api/v1/forecast-runs/v12/" + run_id,
                    headers={"Authorization": "Bearer " + tokens["outsider"]},
                ).status_code
                == 404,
                "ai10_v12_foreign_scope_hidden",
            )
        batch_run, batch_receipt = queue.get(run_id, pipeline), queue.output(run_id, pipeline)
        events = forecast_events(output, batch_run, batch_receipt)
        with engine.connect() as connection:
            stored = connection.execute(
                text(
                    "SELECT event_id,document,partition_key FROM ai.intelligence_outbox ORDER BY event_id"
                )
            ).all()
        expected = {str(value.event_id): value for value in events}
        require(
            len(stored) == len(events) == len(output.rows) == 56, "ai10_v12_complete_native_census"
        )
        for row in stored:
            value = ForecastGenerated.model_validate_json(json.dumps(row.document))
            require(
                value == expected[str(row.event_id)] and row.partition_key == value.partition_key,
                "ai10_v12_original_sql_payloads",
            )
        page = reader.read(ForecastQuery(inference_run_id=run_id, limit=200), pipeline)
        require(
            {
                item.prediction_id: item.model_dump(mode="json", exclude={"freshness"})
                for item in page.items
            }
            == {
                value.payload.prediction_id: value.payload.model_dump(
                    mode="json", exclude={"freshness"}
                )
                for value in events
            },
            "ai10_v12_native_reader_payloads",
        )
        private_json(args.output / "native-publication.json", output.model_dump(mode="json"))
        private_json(args.output / "native-run.json", batch_run.model_dump(mode="json"))
        private_json(args.output / "native-computation.json", batch_receipt.model_dump(mode="json"))
        event_bytes = b"".join(value.model_dump_json().encode() + b"\n" for value in events)
        (args.output / "events.jsonl").write_bytes(event_bytes)
        (args.output / "events.jsonl").chmod(0o600)
        with journal.locked(DEVELOPMENT_MODEL):
            active = journal.active(DEVELOPMENT_MODEL)
            require(
                active is not None and active.release_id == output.release_id,
                "ai10_v12_original_review_head",
            )
            review = dict(
                model_name=DEVELOPMENT_MODEL,
                model_version=version,
                approval_id=loaded.release.release_id,
                approval_sha256=imported["approval_sha256"],
                rejected=journal.rejected(DEVELOPMENT_MODEL, version),
                release_id=active.release_id if active else None,
                aliases=registry.aliases(DEVELOPMENT_MODEL),
                pending_decisions=journal.pending(DEVELOPMENT_MODEL),
                runtime_status="not_integrated",
                development_acceptance_sha256=acceptance.decision.sha256,
            )
        private_json(args.output / "owner-review.json", review)
        private_json(
            args.output / "development-acceptance.json", acceptance.model_dump(mode="json")
        )
        passed(stage)
        report.update(
            status="passed",
            commit=os.environ["GITHUB_SHA"],
            workflow_run_id=int(os.environ["GITHUB_RUN_ID"]),
            original_run_id=pins["run_id"],
            original_manifest_sha256=pins["run_manifest_sha256"],
            source_dataset_id=output.source_dataset_id,
            snapshot_id=inputs.feature_manifest.descriptor.parent.snapshot_id,
            history_days=102,
            forecast_plan_days=14,
            profile_id=inputs.profile_id,
            run_id=run_id,
            publication_id=output.artifact_id,
            rows=56,
            qualification_id=qualification.name,
            approval_id=approval.name,
            image_digest=image,
            events_sha256=hashlib.sha256(event_bytes).hexdigest(),
            native_publication_sha256=hashlib.sha256(
                (args.output / "native-publication.json").read_bytes()
            ).hexdigest(),
            actual_original_full_semantic_verifier=True,
            actual_complete_mlflow_http_import_files=664,
            mlflow_archive_storage="shared_inodes_of_owned_disposable_recovered_scratch_not_independent_backup",
            real_postgres=True,
            real_frozen_cold_worker=True,
            actual_HTTP_auth_scope_idempotency=True,
            atomic_outbox_failure_rollback=True,
            original_AI_database_publisher_attested=False,
            source_API_UI_attested=False,
        )
        # Seal the completed original model/SQL stage before the independent Source
        # reader starts. Original owned PostgreSQL remains alive through real delivery.
        private_json(args.output / "acceptance.json", report)
        control = work / "original-database-control.json"
        private_json(
            control,
            dict(
                owner=owner,
                database_url=url,
                publication_id=output.artifact_id,
                commit=os.environ["GITHUB_SHA"],
                workflow_run_id=int(os.environ["GITHUB_RUN_ID"]),
                event_sha256={
                    str(value.event_id): hashlib.sha256(
                        value.model_dump_json().encode()
                    ).hexdigest()
                    for value in events
                },
            ),
        )
        stage = "original_sql_delivery_complete_Source_API_and_built_browser"
        child_env = {
            **os.environ,
            "PYTHONPATH": str(args.consumer_root.resolve())
            + os.pathsep
            + str((args.consumer_root / "services/api").resolve()),
            "REQUIRE_BROKER_TESTS": "1",
            "REQUIRE_AI10_NATIVE_FORECAST_READ": "1",
            "AI10_NATIVE_FORECAST_OUTPUT": str(args.output.resolve()),
            "AI10_NATIVE_PRODUCER_COMMIT": os.environ["GITHUB_SHA"],
            "AI10_NATIVE_READ_REPORT": str((args.output / "source-native-read.json").resolve()),
            "AI10_V12_ORIGINAL_DATABASE_CONTROL": str(control.resolve()),
            "AI10_NATIVE_DELIVERY_PYTHON": str(
                ROOT / "tools/intelligence-delivery/.venv/bin/python"
            ),
            "AI10_NATIVE_DELIVERY_SCRIPT": str(ROOT / "scripts/deliver_ai10_v12_native.py"),
        }
        with (work / "source-consumer.log").open("wb") as log:
            completed = subprocess.run(  # noqa: S603 - fixed test in verified pinned Source checkout
                [
                    str((args.consumer_root / "services/api/.venv/bin/python").resolve()),
                    "-m",
                    "pytest",
                    "-q",
                    "-x",
                    "services/api/tests/test_native_forecast_output_durability.py",
                    "--junitxml=" + str((work / "source-native-tests.xml").resolve()),
                ],
                cwd=args.consumer_root,
                env=child_env,
                stdout=log,
                stderr=log,
                timeout=600,
                check=False,
            )
        if completed.returncode:
            report["source_failure_tests"] = re.findall(
                r"FAILED (services/api/tests/[a-zA-Z0-9_/.]+::[a-zA-Z0-9_]+)",
                (work / "source-consumer.log").read_text(),
            )
        require(completed.returncode == 0, "ai10_v12_original_source_consumer_failed")
        shutil.copyfile(work / "source-native-tests.xml", args.output / "source-native-tests.xml")
        (args.output / "source-native-tests.xml").chmod(0o600)
        source_report = read_json(args.output, "source-native-read.json")
        require(
            source_report["status"] == "passed"
            and source_report["source_commit"] == pins["source_commit"]
            and source_report["original_AI_database_publisher_attested"] is True
            and source_report["native_payloads_unchanged"] is True
            and source_report["rows"] == 56
            and source_report["browser"]["status"] == "passed",
            "ai10_v12_complete_independent_source_attestation",
        )
        passed(stage)
        report.update(
            original_AI_database_publisher_attested=True,
            source_API_UI_attested=True,
            source_commit=pins["source_commit"],
            source_scope=source_report["scope"],
        )
    except Exception as error:
        report.update(status="failed", failed_stage=stage, exception_type=type(error).__name__)
        if isinstance(error, ValueError) and re.fullmatch(r"[a-z0-9_]{1,160}", str(error)):
            report["failure_category"] = str(error)
    finally:
        if engine is not None:
            engine.dispose()
        try:
            for name in reversed(owned):
                label = docker(
                    "inspect",
                    "--format",
                    '{{ index .Config.Labels "retailops.ai10.v12.owner" }}',
                    name,
                )
                require(label == owner, "ai10_v12_cleanup_ownership")
                docker("rm", "-fv", name)
            report["owned_containers_and_anonymous_volumes_removed"] = True
        except Exception:
            report.update(status="failed", failed_stage="owned_cleanup")
        if created_output:
            private_json(args.output / "acceptance.json", report)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "failed_stage",
                    "failure_category",
                    "rows",
                    "original_quality_status",
                )
                if key in report
            }
        ),
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
