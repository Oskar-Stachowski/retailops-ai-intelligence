"""Real local persistence acceptance; no provider fakes, no secret-valued output."""

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

import local_stack as stack
from verify_rag_index import run_in_compose

from retailops_ai.adapters.database import EXPECTED_REVISION


def command(*args: str, stdin: str | None = None, expect: int = 0) -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker_unavailable")
    result = subprocess.run(  # noqa: S603 - fixed subcommands only
        [
            docker,
            "compose",
            "-p",
            stack.project_name(),
            "--env-file",
            str(stack.environment_file(create=False)),
            "-f",
            str(stack.ROOT / "compose.yaml"),
            *args,
        ],
        cwd=stack.ROOT,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    if (result.returncode == 0) != (expect == 0):
        for line in result.stderr.splitlines():
            try:
                error = json.loads(line)
            except ValueError:
                continue
            if isinstance(error, dict) and error.get("error") == "rag_database_acceptance_failed":
                kind = error.get("type")
                if isinstance(kind, str) and re.fullmatch(r"[A-Za-z]+", kind):
                    raise RuntimeError("rag_" + kind.lower() + "_failed") from None
        raise RuntimeError("compose_acceptance_command_failed")
    return result.stdout.strip()


def sql(database: str, query: str, *, role: str = "admin", expect: int = 0) -> str:
    identities = {
        "admin": ("retailops_admin", "POSTGRES_PASSWORD"),
        "ai": ("ai_app", "AI_DB_PASSWORD"),
        "mlflow": ("mlflow_app", "MLFLOW_DB_PASSWORD"),
    }
    user, variable = identities[role]
    # Secret is expanded inside container environment, never in argv or stdout.
    shell = (
        'PGPASSWORD="$'
        + variable
        + f'" exec psql -h 127.0.0.1 -U {user} -d {database} -v ON_ERROR_STOP=1 -At'
    )
    return command("exec", "-T", "db", "sh", "-c", shell, stdin=query, expect=expect)


def request(
    path: str,
    *,
    service: str = "api",
    data: dict[str, Any] | None = None,
    method: str | None = None,
    raw: bytes | None = None,
) -> tuple[int, bytes]:
    port = 8081 if service == "api" else 5010
    payload = json.dumps(data).encode() if data is not None else raw
    headers = {"Content-Type": "application/json" if data is not None else "text/plain"}
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=payload, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:  # noqa: S310 - fixed loopback
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def wait_ready() -> None:
    for _ in range(40):
        try:
            if request("/ready")[0] == 200:
                return
        except (OSError, TimeoutError):
            pass
        time.sleep(1)
    raise RuntimeError("readiness_did_not_recover")


def main() -> int:
    report: dict[str, Any] = {
        "checked_at": datetime.now(UTC).isoformat(),
        "kind": "real_local_compose",
        "checks": [],
    }
    stage = "startup"
    try:
        require(stack.main(["up"]) == 0, "stack_startup_failed")
        stage = "schema_and_isolation"
        require(
            sql("retailops_ai", "SELECT version_num FROM ai.alembic_version;", role="ai")
            == EXPECTED_REVISION,
            "unexpected_schema",
        )
        require(
            sql("retailops_ai", "SELECT extversion FROM pg_extension WHERE extname='vector';")
            == "0.8.6",
            "vector_not_installed",
        )
        sql("retailops_mlflow", "SELECT 1;", role="ai", expect=1)
        sql("retailops_ai", "SELECT 1;", role="mlflow", expect=1)
        require(
            sql(
                "postgres",
                "SELECT count(*) FROM pg_roles WHERE rolname IN ('ai_app','mlflow_app') AND (rolsuper OR rolcreatedb OR rolcreaterole);",
            )
            == "0",
            "application_roles_too_privileged",
        )
        report["checks"].append("separate_databases_roles_and_vector")
        stage = "api_migration_boundary"
        try:
            sql("retailops_ai", "UPDATE ai.alembic_version SET version_num='outdated';", role="ai")
            require(request("/ready")[0] == 503, "stale_schema_was_ready")
            require(request("/health")[0] == 200, "health_coupled_to_schema")
            command("restart", "api")
            time.sleep(3)
            require(
                sql("retailops_ai", "SELECT version_num FROM ai.alembic_version;", role="ai")
                == "outdated",
                "startup_auto_migrated",
            )
        finally:
            sql(
                "retailops_ai",
                f"UPDATE ai.alembic_version SET version_num='{EXPECTED_REVISION}';",  # noqa: S608 - repository constant
                role="ai",
            )
        command("run", "--rm", "api-migrate")
        wait_ready()
        report["checks"].append("explicit_idempotent_migration_and_revision_readiness")
        stage = "rag_candidate_pgvector"
        rag = run_in_compose(command)
        report["rag"] = rag
        report["checks"].append("real_pgvector_candidate_constraints_cache_atomicity_and_replay")
        rag_index_id = str(rag["index_id"])
        require(
            re.fullmatch(r"index-sha256-[0-9a-f]{64}", rag_index_id) is not None,
            "invalid_rag_smoke_id",
        )
        rag_retention_query = (
            f"SELECT count(*) FROM ai.rag_index_chunks WHERE index_id='{rag_index_id}';"  # noqa: S608 - full regex validation above
        )
        stage = "write_ai_and_mlflow"
        sql(
            "retailops_ai",
            """INSERT INTO ai.service_metadata(name,value) VALUES ('persistence_smoke','{"proof":"retained"}')
            ON CONFLICT(name) DO UPDATE SET value=excluded.value;""",
            role="ai",
        )
        experiment_name = "local-persistence-" + str(time.time_ns())
        code, body = request(
            "/api/2.0/mlflow/experiments/create", service="mlflow", data={"name": experiment_name}
        )
        require(code == 200, "mlflow_experiment_failed")
        experiment_id = json.loads(body)["experiment_id"]
        code, body = request(
            "/api/2.0/mlflow/runs/create",
            service="mlflow",
            data={"experiment_id": experiment_id, "start_time": int(time.time() * 1000)},
        )
        require(code == 200, "mlflow_run_failed")
        info = json.loads(body)["run"]["info"]
        artifact_path = info["artifact_uri"].removeprefix("mlflow-artifacts:/").lstrip("/")
        artifact_endpoint = "/api/2.0/mlflow-artifacts/artifacts/" + artifact_path + "/proof.txt"
        artifact = b"retailops-local-persistence-proof\n"
        require(
            request(artifact_endpoint, service="mlflow", method="PUT", raw=artifact)[0] == 200,
            "mlflow_artifact_write_failed",
        )
        stage = "crash_and_restart"
        command("kill", "-s", "SIGKILL", "api", "mlflow", "db")
        command("up", "-d", "--wait", "db", "api", "mlflow")
        wait_ready()
        require(
            sql("retailops_ai", rag_retention_query, role="ai") == str(rag["chunks"]),
            "rag_data_lost_after_crash",
        )
        require(
            sql(
                "retailops_ai",
                "SELECT value->>'proof' FROM ai.service_metadata WHERE name='persistence_smoke';",
                role="ai",
            )
            == "retained",
            "ai_data_lost_after_crash",
        )
        require(
            request(artifact_endpoint, service="mlflow")[1] == artifact, "artifact_lost_after_crash"
        )
        report["checks"].append("sigkill_restart_retains_ai_and_mlflow_artifact")
        stage = "database_outage_recovery"
        command("stop", "db")
        require(request("/health")[0] == 200, "health_failed_during_outage")
        code, body = request("/ready")
        require(code == 503, "outage_not_detected")
        readiness = json.loads(body)["readiness"]
        require(
            readiness["role"] == "ai_api"
            and any(
                d["name"] == "ai_db" and d["status"] in {"down", "timeout"}
                for d in readiness["dependencies"]
            ),
            "database_probe_missing",
        )
        command("up", "-d", "--wait", "db")
        wait_ready()
        report["checks"].append("real_db_outage_health_200_ready_503_then_recovered_200")
        stage = "compose_down_up"
        require(stack.main(["down"]) == 0, "stack_shutdown_failed")
        require(stack.main(["up"]) == 0, "stack_recreation_failed")
        wait_ready()
        require(
            sql("retailops_ai", rag_retention_query, role="ai") == str(rag["chunks"]),
            "rag_data_lost_after_down",
        )
        require(
            sql(
                "retailops_ai",
                "SELECT value->>'proof' FROM ai.service_metadata WHERE name='persistence_smoke';",
                role="ai",
            )
            == "retained",
            "ai_data_lost_after_down",
        )
        code, body = request(
            "/api/2.0/mlflow/experiments/get?experiment_id=" + experiment_id, service="mlflow"
        )
        require(
            code == 200 and json.loads(body)["experiment"]["name"] == experiment_name,
            "mlflow_metadata_lost",
        )
        require(
            request(artifact_endpoint, service="mlflow")[1] == artifact, "artifact_lost_after_down"
        )
        report["checks"].append("down_up_retains_ai_mlflow_metadata_and_artifact")
        stage = "network_logs_security"
        config = json.loads(command("config", "--format", "json"))
        require("ports" not in config["services"]["db"], "database_published")
        for name in ("api", "mlflow"):
            require(
                all(p["host_ip"] == "127.0.0.1" for p in config["services"][name]["ports"]),
                "nonlocal_port",
            )
        logs = command("logs", "--no-color", "db", "api", "mlflow")
        secret_values = [line.partition("=")[2] for line in stack.LOCAL.read_text().splitlines()]
        require(all(secret not in logs for secret in secret_values), "secret_found_in_logs")
        require(request("/metrics")[0] == 401, "metrics_unauthenticated")
        require(request("/health", service="mlflow")[0] == 200, "mlflow_not_healthy")
        report["checks"].append("loopback_ports_internal_network_no_secrets_in_service_logs")
        require(command("port", "api", "8081") == "127.0.0.1:8081", "api_port_not_published")
        require(command("port", "mlflow", "5000") == "127.0.0.1:5010", "mlflow_port_not_published")
        stage = "final_shutdown"
        require(stack.main(["down"]) == 0, "final_shutdown_failed")
        report["result"] = "passed"
        report["api_health"] = 200
        report["api_ready_after_recovery"] = 200
        report["postgres_vector_version"] = "0.8.6"
        report["migration_revision"] = EXPECTED_REVISION
        stack.LOCAL.parent.mkdir(exist_ok=True)
        (stack.LOCAL.parent / "persistence-smoke.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        print(json.dumps(report, indent=2))
        return 0
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, RuntimeError) and re.fullmatch(r"[a-z_]+", str(exc))
            else "unexpected_runtime_failure"
        )
        print(
            json.dumps(
                {"error": "local_persistence_acceptance_failed", "stage": stage, "reason": reason}
            )
        )
        return 1
    finally:
        if report.get("result") != "passed":
            stack.main(["down"])


if __name__ == "__main__":
    raise SystemExit(main())
