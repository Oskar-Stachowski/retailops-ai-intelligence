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

ROOT = Path(__file__).resolve().parents[1]
PRODUCER_COMMIT = "9c000bcc00ea8ec03a45d49ee2ecf49d13de1cf7"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def command(args: list[str], *, cwd: Path = ROOT, log: Path | None = None) -> str:
    result = subprocess.run(  # noqa: S603 - fixed executables, explicit task-owned paths
        args, cwd=cwd, capture_output=True, text=True, check=False, timeout=3000
    )
    if log is not None:
        log.write_text(result.stdout + result.stderr)
    require(result.returncode == 0, "anomaly_oci_child_command_failed")
    return result.stdout.strip()


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
                    "ai-07-portfolio-v3",
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
                    "scripts/data/prepare_ai07_portfolio.py",
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
            prepared_receipt = args.prepared_receipt.resolve()
        prepared = json.loads(prepared_receipt.read_bytes())
        require(
            prepared["status"] == "passed" and prepared["truth_access"] == "excluded",
            "anomaly_oci_native_public_parents",
        )
        mark("native_complete_public_source_snapshot_dq_coverage_features")
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
        command([*base, "up", "-d", "--wait", "mlflow"], log=work / "mlflow.log")
        mark("actual_built_oci_pinned_pg16_mlflow_and_all_migrations")
        output = work / "acceptance"
        output.mkdir(mode=0o700)
        # Bind only public native inference artifacts; producer truth/facts are not mounted.
        mounts = [
            ROOT / "scripts",
            work / "primary",
            work / "reference",
            prepared_receipt,
            ROOT / "data/generated",
            Path(prepared["parents"][1]),
        ]
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
        for path in mounts:
            invocation += ["--volume", str(path) + ":" + str(path) + ":ro"]
        invocation += [
            "--volume",
            str(output) + ":" + str(output),
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
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        return {
            "status": "passed",
            "report": str(args.report),
            "oci_image_digest": image,
            "transport_durability": "offline_only",
        }
    finally:
        if owned:
            command([*base, "down", "--volumes", "--remove-orphans"], log=work / "cleanup.log")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--prepared-receipt", type=Path)
    parser.add_argument("--report", type=Path, default=ROOT / "reports/ai07-oci-acceptance.json")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args)), flush=True)
    except Exception as error:
        result = {"error": "anomaly_oci_acceptance_failed", "exception_type": type(error).__name__}
        if isinstance(error, ValueError) and re.fullmatch(r"[a-z0-9_]{1,120}", str(error)):
            result["check"] = str(error)
        print(json.dumps(result), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
