"""Accept actual qualified models, native MLflow/PostgreSQL, pinned batch and scoped read APIs."""

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from anomaly_acceptance_http import HTTPClient
from log_anomaly_capsule import log
from sqlalchemy import create_engine, text

from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.anomaly_portfolio.batch import batch
from retailops_ai.anomaly_portfolio.cli import prepared, protocol
from retailops_ai.anomaly_portfolio.journal import PostgresJournal
from retailops_ai.anomaly_portfolio.lifecycle import Lifecycle
from retailops_ai.anomaly_portfolio.lifecycle_contract import MODEL, Request
from retailops_ai.anomaly_portfolio.registry import AnomalyRegistry
from retailops_ai.anomaly_portfolio.result_store import PostgresResults
from retailops_ai.anomaly_portfolio.serving_contract import Query
from retailops_ai.api.app import create_app
from retailops_ai.config import load_settings
from retailops_ai.model_lifecycle.anomaly_evaluation_store import PostgresAnomalyEvaluations
from retailops_ai.security.model_operator import model_operator, private_principal
from retailops_ai.security.provision import provision
from retailops_ai.source_snapshot.files import decode_json, read_bytes


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def document(path: Path) -> dict[str, Any]:
    return decode_json(read_bytes(path.parent, path.name, 8 * 1024**2))


class CrashAfterCreate(AnomalyRegistry):
    def create(self, model: str, run_id: str, source: str, decision: str, digest: str) -> str:
        super().create(model, run_id, source, decision, digest)
        raise ValueError("acceptance_crash_after_native_version_creation")


class CrashAfterChampion(AnomalyRegistry):
    def set_alias(self, model: str, alias: str, version: str) -> None:
        super().set_alias(model, alias, version)
        if alias == "champion":
            raise ValueError("acceptance_crash_after_native_alias_write")


def cli(
    module: str,
    args: list[str],
    body: dict[str, Any],
    *,
    expected: int = 0,
) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603 - fixed installed module and explicit private files
        [sys.executable, "-m", module, *args],
        input=json.dumps(body),
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    require(result.returncode == expected, "anomaly_acceptance_private_cli_result")
    if expected:
        require(result.stdout == "", "anomaly_acceptance_error_stdout_disclosure")
        require('"error"' in result.stderr, "anomaly_acceptance_safe_cli_error")
        return {}
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings()
    require(settings.app_env == "test", "anomaly_acceptance_requires_test_environment")
    require(settings.database_url is not None, "anomaly_acceptance_requires_owned_database")
    if settings.database_url is None:
        raise ValueError("anomaly_acceptance_database")
    require(
        bool(re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_digest)),
        "anomaly_acceptance_actual_image_pin",
    )
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    engine = create_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
    )
    registry_args: dict[str, Any] = {
        "port": args.registry_port,
        "compose": settings.network_mode == "compose",
        "environment": "test",
    }
    registry = AnomalyRegistry(**registry_args)
    journal = PostgresJournal(engine)
    results = PostgresResults(engine)
    checkpoint = args.output / "checkpoint.json"
    checks: list[str] = []
    try:
        if args.inspect:
            before = document(checkpoint)
            with journal.locked(MODEL):
                active = journal.active(MODEL)
                require(
                    active is not None and active.release_id == before["release_id"],
                    "anomaly_restart_exact_release",
                )
                require(not journal.pending(MODEL), "anomaly_restart_pending_decision")
                require(
                    journal.rejected(MODEL, before["rejected_version"]), "anomaly_restart_rejection"
                )
                for version in before["versions"]:
                    registry.validate(journal.binding(MODEL, version))
                require(registry.aliases(MODEL) == before["aliases"], "anomaly_restart_aliases")
            with engine.connect() as connection:
                counts = {
                    name: connection.scalar(text("SELECT count(*) FROM ai." + name))  # noqa: S608 - fixed table allowlist below
                    for name in (
                        "anomaly_batches",
                        "anomaly_results",
                        "anomaly_evaluations",
                        "anomaly_requests",
                    )
                }
            require(counts == before["database_counts"], "anomaly_restart_complete_results")
            return {
                "status": "passed",
                "checks": [
                    "native_restart_preserves_saved_models_aliases_releases_evaluations_complete_batches"
                ],
                "release_id": before["release_id"],
            }
        with engine.connect() as connection:
            require(
                connection.scalar(text("SELECT count(*) FROM ai.anomaly_model_decisions")) == 0,
                "anomaly_acceptance_fresh_journal",
            )
        require(registry.aliases(MODEL) == {}, "anomaly_acceptance_fresh_registry")
        saved = document(args.primary / "config.json")["selection"]["descriptor"]["protocol"]
        scopes = saved["scopes"]
        products = sorted({s["product_id"] for s in scopes})
        locations = sorted({s["selling_location_id"] for s in scopes})
        channels = sorted({s["channel"] for s in scopes})
        public_scope = {
            "product_ids": products,
            "selling_location_ids": locations,
            "channels": channels,
        }
        grants = args.output / "grants.json"
        grants.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "policy_id": "ai07-owned-acceptance",
                    "grants": [
                        {
                            "principal_id": "ai07-promoter",
                            "roles": ["promoter"],
                            "capabilities": ["model:decide"],
                            "scope": None,
                            "knowledge_scope": None,
                        },
                        {
                            "principal_id": "ai07-pipeline",
                            "roles": ["pipeline"],
                            "capabilities": ["anomaly:run"],
                            "scope": public_scope,
                            "knowledge_scope": None,
                        },
                        {
                            "principal_id": "ai07-reader",
                            "roles": ["viewer"],
                            "capabilities": ["anomaly:read"],
                            "scope": public_scope,
                            "knowledge_scope": None,
                        },
                        {
                            "principal_id": "ai07-partial-reader",
                            "roles": ["viewer"],
                            "capabilities": ["anomaly:read"],
                            "scope": {**public_scope, "product_ids": products[:1]},
                            "knowledge_scope": None,
                        },
                    ],
                }
            )
        )
        provision(grants, args.output / "access", 1)
        policy = args.output / "access/api-access-policy.json"
        credentials = document(args.output / "access/api-client-credentials.json")["credentials"]
        private: dict[str, Path] = {}
        tokens: dict[str, str] = {}
        for value in credentials:
            path = args.output / (value["principal_id"] + "-credentials.json")
            path.write_text(json.dumps({"schema_version": "1.0", "credentials": [value]}))
            path.chmod(0o600)
            private[value["principal_id"]] = path
            tokens[value["principal_id"]] = value["bearer_token"]
        promoter = model_operator(policy, private["ai07-promoter"])
        pipeline = private_principal(policy, private["ai07-pipeline"])
        reader = private_principal(policy, private["ai07-reader"])
        lifecycle = Lifecycle(registry, journal, environment="test")
        logged = [log(path, registry) for path in (args.primary, args.reference)]
        qualifications = [
            document(path / "qualification.json") for path in (args.primary, args.reference)
        ]

        def request(
            action: str, index: int, version: str | None = None, suffix: str = ""
        ) -> Request:
            return Request.model_validate(
                {
                    "decision_id": f"decision-ai07-{action}-{index + 1:02d}" + suffix,
                    "action": action,
                    "mlflow_run_id": logged[index]["mlflow_run_id"]
                    if action == "register"
                    else None,
                    "model_version": version if action != "register" else None,
                    "evidence_id": qualifications[index]["evidence_id"],
                    "qualification_sha256": logged[index]["qualification_sha256"],
                    "reason": "Native acceptance of the frozen qualified anomaly portfolio",
                    "image_digest": args.image_digest if action == "promote" else None,
                }
            )

        def register_evaluation(version: str) -> None:
            with journal.locked(MODEL):
                binding = journal.binding(MODEL, version)
            uri = binding.source_uri
            PostgresAnomalyEvaluations(engine).register(
                binding,
                decode_json(registry.artifact(uri, "gate_segments.json", limit=8 * 1024**2)),
                decode_json(registry.artifact(uri, "config.json", limit=8 * 1024**2)),
                lambda name: registry.artifact(uri, name, limit=8 * 1024**2),
            )

        operator_args = [
            "--policy-file",
            str(policy),
            "--credentials-file",
            str(private["ai07-promoter"]),
        ]
        if args.registry_port is not None:
            operator_args += ["--registry-port", str(args.registry_port)]
        first = request("register", 0)
        enrolled = cli(
            "retailops_ai.anomaly_portfolio.operator_cli",
            operator_args,
            first.model_dump(mode="json"),
        )
        one = enrolled["model_version"]
        require(
            enrolled["evaluation_projection"] == "persisted_recomputed",
            "anomaly_registration_actual_projection",
        )
        require(
            cli(
                "retailops_ai.anomaly_portfolio.operator_cli",
                operator_args,
                first.model_dump(mode="json"),
            )["replayed"],
            "anomaly_registration_cli_idempotence",
        )
        cli(
            "retailops_ai.anomaly_portfolio.operator_cli",
            ["--policy-file", str(policy), "--credentials-file", str(private["ai07-pipeline"])],
            first.model_dump(mode="json"),
            expected=2,
        )
        lifecycle.execute(request("promote", 0, one), promoter)
        with journal.locked(MODEL):
            release_one = journal.active(MODEL)
        require(release_one is not None, "anomaly_primary_release")
        if release_one is None:
            raise ValueError("anomaly_primary_release_missing")
        checks += [
            "authenticated_private_register_and_idempotent_projection",
            "pipeline_cannot_make_model_decisions",
            "qualified_primary_promoted_with_actual_image_pin",
        ]
        second = request("register", 1)
        try:
            Lifecycle(
                CrashAfterCreate(**registry_args), PostgresJournal(engine), environment="test"
            ).execute(second, promoter)
        except ValueError as exc:
            require(
                str(exc) == "acceptance_crash_after_native_version_creation",
                "anomaly_native_creation_crash",
            )
        else:
            raise ValueError("anomaly_creation_crash_not_exercised")
        try:
            lifecycle.execute(request("register", 0, suffix="-blocked"), promoter)
        except ValueError as exc:
            require(
                str(exc) == "model_incomplete_decision_requires_recovery",
                "anomaly_pending_decision_barrier",
            )
        else:
            raise ValueError("anomaly_pending_decision_was_not_blocking")

        def recover() -> dict[str, Any]:
            return Lifecycle(registry, PostgresJournal(engine), environment="test").execute(
                second, promoter
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            recovered = list(pool.map(lambda _: recover(), range(2)))
        require(
            len({r["model_version"] for r in recovered}) == 1,
            "anomaly_recovery_created_duplicate_version",
        )
        require(
            sorted(r["replayed"] for r in recovered) == [False, True],
            "anomaly_concurrent_recovery_not_serialized",
        )
        two = recovered[0]["model_version"]
        register_evaluation(two)
        promote_two = request("promote", 1, two)
        try:
            Lifecycle(
                CrashAfterChampion(**registry_args), PostgresJournal(engine), environment="test"
            ).execute(promote_two, promoter)
        except ValueError as exc:
            require(
                str(exc) == "acceptance_crash_after_native_alias_write",
                "anomaly_native_alias_crash",
            )
        else:
            raise ValueError("anomaly_alias_crash_not_exercised")
        lifecycle.execute(promote_two, promoter)
        with journal.locked(MODEL):
            release_two = journal.active(MODEL)
        require(release_two is not None, "anomaly_reference_release")
        third = lifecycle.execute(request("register", 1, suffix="-reject"), promoter)[
            "model_version"
        ]
        register_evaluation(third)
        lifecycle.execute(request("reject", 1, third, "-reject"), promoter)
        try:
            lifecycle.execute(request("promote", 1, third, "-reject"), promoter)
        except ValueError as exc:
            require(
                str(exc) == "rejected_model_cannot_be_released",
                "anomaly_rejected_candidate_barrier",
            )
        else:
            raise ValueError("anomaly_rejected_candidate_promoted")
        lifecycle.execute(request("rollback", 0, one), promoter)
        with journal.locked(MODEL):
            active = journal.active(MODEL)
        require(
            active is not None
            and active.binding == release_one.binding
            and active.image_digest == release_one.image_digest
            and active.restored_from_release_id == release_one.release_id,
            "anomaly_rollback_exact_original_pins",
        )
        if active is None:
            raise ValueError("anomaly_rollback_release_missing")
        checks += [
            "real_version_creation_crash_reconciled_without_duplicate",
            "concurrent_recovery_serialized_in_postgresql",
            "real_alias_write_crash_reconciled",
            "explicit_rejection_blocks_promotion",
            "rollback_restores_exact_saved_model_and_image",
        ]
        # A live alias change cannot alter an already accepted release or authorize a new decision.
        registry.set_alias(MODEL, "champion", two)
        try:
            lifecycle.execute(request("register", 0, suffix="-drift"), promoter)
        except ValueError as exc:
            require(
                str(exc) == "registry_champion_disagrees_with_approved_release",
                "anomaly_alias_drift_barrier",
            )
        else:
            raise ValueError("anomaly_alias_drift_accepted")
        registry.set_alias(MODEL, "champion", one)
        frame = prepared(args.prepared_receipt)
        split = protocol(frame)
        window = split.test
        as_of = datetime.combine(window.end, datetime.min.time(), UTC) + timedelta(days=4)
        manifest, items = batch(
            active,
            args.primary / "model.json",
            frame,
            split.scopes,
            window,
            as_of,
            datetime.now(UTC),
        )
        published = results.publish("ai07-native-publish", pipeline, active, manifest, items)
        require(published["status"] == "published", "anomaly_actual_complete_publication")
        require(
            results.publish("ai07-native-publish", pipeline, active, manifest, items)["status"]
            == "replayed",
            "anomaly_publication_idempotence",
        )
        later, later_items = batch(
            active,
            args.primary / "model.json",
            frame,
            split.scopes,
            window,
            as_of,
            datetime.now(UTC) + timedelta(seconds=5),
        )
        require(
            results.publish("ai07-native-reuse", pipeline, active, later, later_items)["status"]
            == "reused",
            "anomaly_complete_batch_reuse_preserves_original_time",
        )
        # Force a real PostgreSQL failure during row insertion; the full batch and request roll back.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE FUNCTION ai.ai07_acceptance_fail_row() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.position=1 THEN RAISE EXCEPTION 'ai07_test_insert_failure'; END IF; RETURN NEW; END $$"
                )
            )
            connection.execute(
                text(
                    "CREATE TRIGGER ai07_acceptance_fail_row BEFORE INSERT ON ai.anomaly_results FOR EACH ROW EXECUTE FUNCTION ai.ai07_acceptance_fail_row()"
                )
            )
        failed_manifest, failed_items = batch(
            active,
            args.primary / "model.json",
            frame,
            split.scopes,
            Window(start=window.start, end=window.start + timedelta(days=1)),
            as_of,
            datetime.now(UTC),
        )
        try:
            try:
                results.publish(
                    "ai07-native-failure", pipeline, active, failed_manifest, failed_items
                )
            except Exception:
                with engine.connect() as connection:
                    require(
                        connection.scalar(
                            text("SELECT count(*) FROM ai.anomaly_batches WHERE batch_id=:id"),
                            {"id": failed_manifest["batch_id"]},
                        )
                        == 0,
                        "anomaly_partial_batch_persisted",
                    )
                    require(
                        connection.scalar(
                            text(
                                "SELECT count(*) FROM ai.anomaly_requests WHERE request_id='ai07-native-failure'"
                            )
                        )
                        == 0,
                        "anomaly_failed_request_persisted",
                    )
            else:
                raise ValueError("anomaly_database_failure_not_exercised")
        finally:
            with engine.begin() as connection:
                connection.execute(
                    text("DROP TRIGGER ai07_acceptance_fail_row ON ai.anomaly_results")
                )
                connection.execute(text("DROP FUNCTION ai.ai07_acceptance_fail_row()"))
        checks += [
            "external_alias_drift_blocks_decisions",
            "actual_saved_model_scores_native_verified_public_features",
            "complete_census_atomic_postgresql_publication",
            "idempotency_and_logical_reuse_keep_original_detection_times",
            "native_insert_failure_leaves_no_partial_batch_or_request",
        ]
        all_rows = results.read(Query(limit=100), reader)
        require(all_rows.pagination.total == len(items), "anomaly_native_read_census")
        app_settings = settings.model_copy(update={"api_auth_file": policy})
        with HTTPClient(create_app(app_settings)) as client:
            full = {"Authorization": "Bearer " + tokens["ai07-reader"]}
            partial = {"Authorization": "Bearer " + tokens["ai07-partial-reader"]}
            require(
                client.get("/api/v1/anomalies").status_code == 401,
                "anomaly_api_requires_authentication",
            )
            response = client.get("/api/v1/anomalies?limit=2", headers=full)
            require(response.status_code == 200, "anomaly_api_native_read")
            page = response.json()
            require(
                page["pagination"]["total"] == len(items), "anomaly_api_count_after_scope_filter"
            )
            require(
                client.post("/api/v1/anomalies", headers=full, json={}).status_code == 405,
                "anomaly_api_read_only",
            )
            require(
                client.get(
                    "/api/v1/anomalies?product_id=" + products[-1], headers=partial
                ).status_code
                == 403,
                "anomaly_api_forbidden_scope",
            )
            invisible = next(i.anomaly_id for i in items if i.product_id == products[-1])
            require(
                client.get("/api/v1/anomalies/" + invisible, headers=partial).status_code == 404,
                "anomaly_api_invisible_identity",
            )
            require(
                client.get("/api/v1/models/" + MODEL, headers=full).status_code == 200,
                "anomaly_common_models_native",
            )
            evaluation = enrolled["evaluation_id"]
            require(
                client.get("/api/v1/evaluations/" + evaluation, headers=full).status_code == 200,
                "anomaly_common_evaluations_native",
            )
            require(
                client.get("/api/v1/evaluations/" + evaluation, headers=partial).status_code == 404,
                "anomaly_evaluation_whole_scope_authorization",
            )
            require(
                "episode_id"
                not in client.get("/api/v1/evaluations/" + evaluation, headers=full).text,
                "anomaly_public_evaluation_private_truth",
            )
            for item in page["items"]:
                require(
                    item["detector_version"] == one and item["release_id"] == active.release_id,
                    "anomaly_api_exact_release_lineage",
                )
                require(
                    item["freshness_status"]
                    == ("unknown" if item["status"] == "insufficient_data" else "stale"),
                    "anomaly_api_historical_freshness",
                )
        checks += [
            "scoped_read_api_counts_403_404_and_read_only_routes",
            "common_model_and_evaluation_metadata_on_native_persistence",
            "historical_stale_and_unknown_inputs_reported_honestly",
        ]
        with engine.connect() as connection:
            counts = {
                name: connection.scalar(text("SELECT count(*) FROM ai." + name))  # noqa: S608 - fixed table allowlist below
                for name in (
                    "anomaly_batches",
                    "anomaly_results",
                    "anomaly_evaluations",
                    "anomaly_requests",
                )
            }
        output = {
            "status": "passed",
            "checks": checks,
            "release_id": active.release_id,
            "versions": [one, two, third],
            "rejected_version": third,
            "aliases": registry.aliases(MODEL),
            "database_counts": counts,
            "batch_id": manifest["batch_id"],
            "model_sha256": active.binding.qualification.model.sha256,
            "image_digest": args.image_digest,
            "qualification_scope": active.binding.qualification.qualification_scope,
            "transport_durability": "offline_only",
        }
        checkpoint.write_text(json.dumps(output, indent=2) + "\n")
        return output
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("primary", "reference", "prepared-receipt", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--registry-port", type=int)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), sort_keys=True), flush=True)
    except Exception as error:
        diagnostic = {
            "error": "anomaly_native_acceptance_failed",
            "exception_type": type(error).__name__,
        }
        if isinstance(error, ValueError) and re.fullmatch(r"[a-z0-9_]{1,120}", str(error)):
            diagnostic["check"] = str(error)
        print(json.dumps(diagnostic), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
