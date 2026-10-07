"""Fresh owned OCI/Pg16/MLflow acceptance; rebuild public inference parents, preserve other stacks."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import local_stack
from refresh_anomaly_compatibility import refresh
from run_ai10_model_consumer import run_source_model_read
from sqlalchemy import create_engine

from retailops_ai.source_snapshot.files import read_bytes

ROOT = Path(__file__).resolve().parents[1]
PRODUCER_COMMIT = "48439ebd9515dc1c7adc633609bbe33d1657c3df"


def export_native_output(source: Path, report: dict[str, Any], output: Path) -> None:
    """Copy only the original public output/census after the whole native acceptance succeeds."""
    census_id = report["acceptance"]["native_outbox_census_id"]
    require(
        report["status"] == "passed"
        and bool(re.fullmatch(r"native-model-outbox-sha256-[0-9a-f]{64}", census_id)),
        "anomaly_native_completed_census_required",
    )
    names = [
        "native-batch-output.json",
        "native-frozen-model.json",
        "native-outbox/" + census_id + "/receipt.json",
        "native-outbox/" + census_id + "/events.jsonl",
    ]
    files = {name: read_bytes(source, name, 64 * 1024**2) for name in names}
    output.mkdir(mode=0o700)
    try:
        for name, raw in files.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(raw)
            target.chmod(0o600)
        acceptance = output / "acceptance.json"
        acceptance.write_text(json.dumps(report, indent=2) + "\n")
        acceptance.chmod(0o600)
    except BaseException:
        shutil.rmtree(output)
        raise


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def command(args: list[str], *, cwd: Path = ROOT, log: Path | None = None) -> str:
    result = subprocess.run(  # noqa: S603 - fixed executables, explicit task-owned paths
        args, cwd=cwd, capture_output=True, text=True, check=False, timeout=3000
    )
    if log is not None:
        log.write_text(
            result.stdout
            + result.stderr
            + "\n"
            + json.dumps({"child_exit_code": result.returncode})
            + "\n"
        )
        if result.returncode and log.name in {
            "source.log",
            "source-preparation.log",
            "public-input-preparation.log",
        }:
            # These pre-container jobs contain only approved generated
            # synthetic facts. Keep their bounded failure detail visible in CI;
            # service logs/configuration can contain credentials and stay private.
            print(
                json.dumps(
                    {
                        "failed_native_stage": log.stem,
                        "child_exit_code": result.returncode,
                        "detail": (result.stdout + result.stderr)[-6000:],
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
    require(result.returncode == 0, "anomaly_oci_child_command_failed")
    return result.stdout.strip()


def original_database_binding(docker: str, base: list[str], project: str) -> str:
    """Read the actual owned DB mapping, including after the required SIGKILL restart."""
    container = command([*base, "ps", "-q", "db"])
    require(bool(re.fullmatch(r"[0-9a-f]{12,64}", container)), "anomaly_original_database_required")
    inspected = json.loads(command([docker, "inspect", container]))
    require(len(inspected) == 1, "anomaly_original_database_required")
    info = inspected[0]
    labels = info["Config"]["Labels"]
    require(
        labels.get("com.docker.compose.project") == project
        and labels.get("com.docker.compose.service") == "db"
        and info["State"]["Running"] is True,
        "anomaly_original_database_owner_required",
    )
    mappings = info["NetworkSettings"]["Ports"].get("5432/tcp") or []
    require(
        len(mappings) == 1
        and mappings[0].get("HostIp") == "127.0.0.1"
        and re.fullmatch(r"[0-9]{1,5}", mappings[0].get("HostPort", "")) is not None
        and 0 < int(mappings[0]["HostPort"]) <= 65535,
        "anomaly_original_database_loopback_required",
    )
    return "127.0.0.1:" + str(mappings[0]["HostPort"])


def extract_capsule(path: Path, output: Path, expected: dict[str, Any]) -> None:
    raw = path.read_bytes()
    require(
        len(raw) <= 16 * 1024**2 and hashlib.sha256(raw).hexdigest() == expected["sha256"],
        "anomaly_oci_capsule_archive_checksum",
    )
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        require(
            1 <= len(entries) <= 32 and sum(i.file_size for i in entries) <= 64 * 1024**2,
            "anomaly_oci_capsule_archive_budget",
        )
        require(
            len({i.filename for i in entries}) == len(entries),
            "anomaly_oci_capsule_archive_duplicate",
        )
        for info in entries:
            require(
                bool(re.fullmatch(r"[a-z0-9_]+\.json", info.filename))
                and info.file_size <= 8 * 1024**2,
                "anomaly_oci_capsule_archive_path",
            )
            require(
                (info.external_attr >> 16) & 0o170000 in {0, 0o100000},
                "anomaly_oci_capsule_archive_type",
            )
            (output / info.filename).write_bytes(archive.read(info))


def run(args: argparse.Namespace) -> dict[str, Any]:
    docker = shutil.which("docker")
    git = shutil.which("git")
    require(docker is not None and git is not None, "anomaly_oci_native_tools_required")
    if docker is None or git is None:
        raise ValueError("anomaly_oci_tools_missing")
    producer = args.producer.resolve()
    require(
        command([git, "rev-parse", "HEAD"], cwd=producer) == PRODUCER_COMMIT,
        "anomaly_oci_pinned_source_revision",
    )
    work = ROOT / ".local" / ("ai07-oci-" + uuid.uuid4().hex[:10])
    work.mkdir(parents=True, mode=0o700)
    project = "retailops_ai_anomaly_" + uuid.uuid4().hex[:10]
    environment = local_stack.environment_file(create=True)
    override = work / "compose-override.yaml"
    override.write_text(
        "services:\n  api:\n    ports: !reset []\n  mlflow:\n    ports: !reset []\n"
    )
    if args.source_consumer_root is not None:
        require(
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
            and args.native_output is not None,
            "anomaly_original_delivery_owned_runner_required",
        )
        # Only this fresh disposable project's DB gets an ephemeral loopback port.
        # The normal OCI acceptance and original Source producer keep their boundaries.
        override.write_text(
            override.read_text()
            + '  db:\n    ports: ["127.0.0.1::5432"]\n'
            + "    networks: [ai_backend, ai10_delivery]\n"
            + "networks:\n  ai10_delivery:\n    driver: bridge\n    internal: false\n"
        )
    base = [
        docker,
        "compose",
        "-p",
        project,
        "--env-file",
        str(environment),
        "-f",
        str(ROOT / "compose.yaml"),
        "-f",
        str(override),
    ]
    require(command([*base, "ps", "-a", "-q"]) == "", "anomaly_oci_fresh_owned_project")
    owned = False
    stages: list[str] = []

    def mark(stage: str) -> None:
        stages.append(stage)
        print(json.dumps({"stage": stage, "status": "passed"}), flush=True)

    try:
        manifest = json.loads((ROOT / "docs/evidence/07-ready/capsules.json").read_bytes())
        capsules = {}
        for kind in ("primary", "reference"):
            original = work / (kind + "-original")
            extract_capsule(
                ROOT / "docs/evidence/07-ready" / (kind + ".zip"), original, manifest[kind]
            )
            current = work / kind
            refresh(original, current)
            capsules[kind] = current
        mark("saved_models_frozen_evaluation_and_current_compatibility")
        owned = True
        command([*base, "build", "api", "mlflow"], log=work / "build.log")
        image = command([docker, "image", "inspect", project + "-api:local", "--format", "{{.Id}}"])
        require(
            bool(re.fullmatch(r"sha256:[0-9a-f]{64}", image)),
            "anomaly_oci_actual_built_image_digest",
        )
        command([*base, "up", "-d", "--wait", "db"], log=work / "database.log")
        command([*base, "run", "--rm", "-T", "api-migrate"], log=work / "migrations.log")
        command([*base, "run", "--rm", "-T", "mlflow-migrate"], log=work / "mlflow-migration.log")
        command([*base, "up", "-d", "--wait", "api", "mlflow"], log=work / "services.log")
        ready = json.loads(
            command(
                [
                    *base,
                    "exec",
                    "-T",
                    "api",
                    "python",
                    "-c",
                    "import json,urllib.request; "
                    "response=urllib.request.urlopen('http://127.0.0.1:8081/ready',timeout=2); "
                    "print(json.dumps({'status':response.status}))",
                ],
                log=work / "api-readiness.log",
            )
        )
        require(ready["status"] == 200, "anomaly_oci_migrated_api_readiness")
        mark("migrated_api_service_ready_without_host_ports")
        mark("actual_built_oci_pinned_pg16_mlflow_and_all_migrations")
        if args.source_consumer_root is not None:
            original_database_binding(docker, base, project)
            mark("original_AI_database_loopback_preflight")
        if args.prepared_receipt is None:
            native_python = producer / "services/api/.venv/bin/python"
            require(native_python.is_file(), "anomaly_oci_producer_toolchain_required")
            source_receipt = work / "source-receipt.json"
            bundle = work / "source-bundle.json"
            command(
                [
                    str(native_python),
                    "-m",
                    "data.anomalies.run_source",
                    "--profile",
                    "ai-07-portfolio-v4",
                    "--seed",
                    "42",
                    "--portfolio",
                    "demand",
                    "--output-root",
                    "data/generated/ai07-oci/source",
                    "--output",
                    str(source_receipt),
                ],
                cwd=producer,
                log=work / "source.log",
            )
            source = producer / json.loads(source_receipt.read_bytes())["directory"]
            command(
                [
                    str(native_python),
                    "-m",
                    "scripts.data.prepare_ai07_portfolio",
                    "--source",
                    str(source),
                    "--output-root",
                    str(producer / "data/generated/ai07-oci/parents"),
                    "--bundle",
                    str(bundle),
                ],
                cwd=producer,
                log=work / "source-preparation.log",
            )
            prepared_receipt = work / "prepared.json"
            command(
                [
                    sys.executable,
                    "scripts/prepare_anomaly_portfolio.py",
                    "--source-bundle",
                    str(bundle),
                    "--generated-root",
                    str(ROOT / "data/generated"),
                    "--receipt",
                    str(prepared_receipt),
                ],
                log=work / "public-input-preparation.log",
            )
        else:
            # Keep the receipt within the task workspace exposed to the OCI
            # daemon, including when that daemon runs in an isolated VM.
            receipt = args.prepared_receipt.resolve()
            require(receipt.is_file(), "anomaly_oci_prepared_receipt_regular_file")
            prepared_receipt = work / "prepared.json"
            shutil.copyfile(receipt, prepared_receipt)
        prepared = json.loads(prepared_receipt.read_bytes())
        require(
            prepared["status"] == "passed" and prepared["truth_access"] == "excluded",
            "anomaly_oci_native_public_parents",
        )
        mark("native_complete_public_source_snapshot_dq_coverage_features")
        output = work / "acceptance"
        output.mkdir(mode=0o700)
        # Bind only public native inference artifacts; producer truth/facts are not mounted.
        mounts = [
            ROOT / "scripts",
            work / "primary",
            work / "reference",
            prepared_receipt,
            Path(prepared["feature_dir"]),
            *(Path(parent) for parent in prepared["parents"]),
        ]
        bindings = [
            {
                "type": "bind",
                "source": str(path),
                "target": str(path),
                "read_only": path != output,
                "bind": {"create_host_path": False},
            }
            for path in [*mounts, output]
        ]
        override.write_text(
            override.read_text().replace(
                "services:\n",
                "services:\n  api-migrate:\n    volumes: " + json.dumps(bindings) + "\n",
                1,
            )
        )
        invocation = [
            *base,
            "run",
            "--rm",
            "-T",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "APP_ENV=test",
        ]
        invocation += [
            "api-migrate",
            "python",
            str(ROOT / "scripts/accept_anomaly_lifecycle.py"),
            "--primary",
            str(capsules["primary"]),
            "--reference",
            str(capsules["reference"]),
            "--prepared-receipt",
            str(prepared_receipt),
            "--output",
            str(output),
            "--image-digest",
            image,
        ]
        acceptance = json.loads(command(invocation, log=work / "acceptance.log"))
        require(acceptance["status"] == "passed", "anomaly_oci_native_acceptance")
        mark("real_qualified_versions_lifecycle_recovery_atomic_batch_scoped_http")
        command([*base, "kill", "-s", "SIGKILL", "mlflow", "db"], log=work / "sigkill.log")
        command([*base, "up", "-d", "--wait", "db", "mlflow"], log=work / "restart.log")
        restarted = json.loads(
            command([*invocation, "--inspect"], log=work / "restart-inspection.log")
        )
        require(
            restarted["status"] == "passed" and restarted["release_id"] == acceptance["release_id"],
            "anomaly_oci_exact_restart_acceptance",
        )
        mark("sigkill_restart_preserves_exact_complete_state")
        report = {
            "status": "passed",
            "checked_at": datetime.now(UTC).isoformat(),
            "consumer_commit": command([git, "rev-parse", "HEAD"]),
            "producer_commit": PRODUCER_COMMIT,
            "oci_image_digest": image,
            "image_pin_kind": "actual_local_oci_image_configuration_sha256",
            "source_dataset_id": prepared["source_dataset_id"],
            "qualified_anomaly_input_id": prepared["feature_id"],
            "qualification_scope": acceptance["qualification_scope"],
            "acceptance": acceptance,
            "restart": restarted,
            "stages": stages,
            "transport_durability": "offline_only",
            "deployment_attestation": "not_attested",
        }
        if args.native_output is not None:
            require(
                os.getenv("GITHUB_RUN_ID", "").isdecimal(),
                "anomaly_native_workflow_identity_required",
            )
            report["workflow_run_id"] = int(os.environ["GITHUB_RUN_ID"])
            export_native_output(output, report, args.native_output)
            args.native_export_created = True
            if args.source_consumer_root is not None:
                binding = original_database_binding(docker, base, project)
                values = dict(line.split("=", 1) for line in environment.read_text().splitlines())
                url = (
                    f"postgresql+psycopg://ai_app:{values['AI_DB_PASSWORD']}@{binding}/retailops_ai"
                )
                engine = create_engine(
                    url, hide_parameters=True, connect_args={"connect_timeout": 3}
                )
                try:
                    source_report = run_source_model_read(
                        engine=engine,
                        database_url=url,
                        output=args.native_output,
                        consumer=args.source_consumer_root,
                        work=work,
                        kind="anomaly_detected",
                    )
                finally:
                    engine.dispose()
                report.update(
                    transport_durability="original_AI_SQL_publisher_to_Source_SQL_API_and_built_UI",
                    original_AI_database_publisher_attested=True,
                    source_API_UI_attested=True,
                    source_commit=source_report["source_commit"],
                )
                (args.native_output / "acceptance.json").write_text(
                    json.dumps(report, indent=2) + "\n"
                )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        return {
            "status": "passed",
            "report": str(args.report),
            "oci_image_digest": image,
            "transport_durability": report["transport_durability"],
        }
    finally:
        if owned:
            command([*base, "down", "--volumes", "--remove-orphans"], log=work / "cleanup.log")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--prepared-receipt", type=Path)
    parser.add_argument("--native-output", type=Path)
    parser.add_argument("--source-consumer-root", type=Path)
    parser.add_argument("--report", type=Path, default=ROOT / "reports/ai07-oci-acceptance.json")
    args = parser.parse_args()
    args.native_export_created = False
    try:
        print(json.dumps(run(args)), flush=True)
    except Exception as error:
        result = {"error": "anomaly_oci_acceptance_failed", "exception_type": type(error).__name__}
        if isinstance(error, ValueError) and re.fullmatch(r"[a-z0-9_]{1,120}", str(error)):
            result["check"] = str(error)
        if args.native_export_created:
            path = args.native_output / "acceptance.json"
            receipt = json.loads(path.read_bytes())
            receipt.update(
                status="failed",
                failure_category=result.get("check", "anomaly_oci_acceptance_failed"),
            )
            path.write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(result), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
