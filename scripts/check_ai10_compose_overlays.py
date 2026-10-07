"""Compile both existing-project overlays without a daemon or runtime mutation."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
BUS = "ai10-source-owned-config-validation"


def validate(ai: dict[str, Any], source: dict[str, Any]) -> None:
    for config in (ai, source):
        network = config["networks"]["intelligence_bus"]
        if network.get("external") is not True or network.get("name") != BUS:
            raise ValueError("ai10_shared_network_must_be_source_owned_external")
        if "intelligence_bus" in config["services"]["db"]["networks"]:
            raise ValueError("ai10_database_must_remain_on_its_original_private_network")
    for config, alias in ((ai, "retailops-ai"), (source, "retailops-api")):
        if alias not in config["services"]["api"]["networks"]["intelligence_bus"].get(
            "aliases", []
        ):
            raise ValueError("ai10_explicit_cross_project_api_alias_required")
    broker = source["services"]["redpanda"]
    if "intelligence_bus" not in broker["networks"]:
        raise ValueError("ai10_existing_source_broker_must_join_shared_network")
    command = broker["command"]
    advertised = command[command.index("--advertise-kafka-addr") + 1].split(",")
    if "internal://redpanda:9092" not in advertised:
        raise ValueError("ai10_source_advertised_listener_must_resolve_on_shared_network")
    if "redpanda" in ai["services"] or "broker" in ai["services"]:
        raise ValueError("ai10_ai_overlay_must_not_start_a_second_broker")
    delivery = ai["services"]["intelligence-delivery"]
    if set(delivery["networks"]) != {"ai_backend", "intelligence_bus"}:
        raise ValueError("ai10_delivery_requires_only_ai_database_and_source_bus")
    if delivery.get("profiles") != ["intelligence"] or delivery.get("restart") != "no":
        raise ValueError("ai10_delivery_must_be_an_explicit_bounded_operation")
    mounts = [m for m in delivery["volumes"] if m.get("target") == "/private"]
    if len(mounts) != 1 or mounts[0].get("read_only") is not True:
        raise ValueError("ai10_private_configuration_requires_read_only_mount")
    if source["services"]["api"]["networks"].keys() != {"default", "intelligence_bus"}:
        raise ValueError("ai10_source_api_must_preserve_its_original_database_network")
    for name in ("db", "mlflow", "api-migrate", "mlflow-migrate"):
        if "intelligence_bus" in ai["services"][name]["networks"]:
            raise ValueError("ai10_ai_database_and_registry_must_remain_private")


def compile_config(
    root: Path, files: list[str], env: dict[str, str], private: Path
) -> dict[str, Any]:
    docker = shutil.which("docker")
    if docker is None:
        raise ValueError("ai10_compose_cli_required")
    completed = subprocess.run(  # noqa: S603 - fixed config-only command, never up/run/down
        [
            docker,
            "compose",
            "--env-file",
            str(private / "empty.env"),
            "-p",
            "ai10_overlay_config",
            *[arg for name in files for arg in ("-f", str(root / name))],
            "config",
            "--format",
            "json",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("ai10_compose_overlay_compile_failed")
    return cast(dict[str, Any], json.loads(completed.stdout))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_root.resolve()
    with tempfile.TemporaryDirectory(prefix="ai10-overlay-config-") as directory:
        private = Path(directory)
        (private / "empty.env").touch(mode=0o600)
        env = {
            **os.environ,
            "COMPOSE_PROJECT_NAME": "ai10_overlay_config",
            "COMPOSE_PROFILES": "dev,intelligence,maintenance",
            "POSTGRES_PASSWORD": "configuration-only-unused-postgres",
            "AI_DB_PASSWORD": "configuration-only-unused-ai",
            "MLFLOW_DB_PASSWORD": "configuration-only-unused-mlflow",
            "METRICS_TOKEN": "configuration-only-unused-metrics",
            "REDPANDA_KAFKA_PORT": "19092",
            "RETAILOPS_INTELLIGENCE_NETWORK": BUS,
            "AI10_PRIVATE": str(private),
            "AI10_UID": "10001",
            "AI10_GID": "10001",
        }
        ai_files = ["compose.yaml", "infra/compose-intelligence.yaml"]
        source_files = ["docker-compose.yml", "docker-compose.intelligence.yml"]
        validate(
            compile_config(ROOT, ai_files, env, private),
            compile_config(source, source_files, env, private),
        )
    report = {
        "status": "passed",
        "scope": "actual Compose CLI compilation of both existing-project overlays; no daemon, image, network or runtime mutation; model/API/UI qualification is separately attested",
        "external_network_owned_by": "Source",
        "database_isolation": True,
        "existing_source_advertised_broker": "redpanda:9092",
        "api_aliases": ["retailops-api", "retailops-ai"],
        "ai_second_broker": False,
        "ai_cleanup_preserves_external_source_network": True,
        "file_sha256": [
            {
                "repository": repo,
                "path": name,
                "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
            }
            for repo, root, names in (
                ("AI", ROOT, ai_files + ["Dockerfile.intelligence"]),
                ("Source", source, source_files),
            )
            for name in names
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"status": "passed", "scope": "both_existing_project_overlay_configurations"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
