"""Control a checkout-isolated stack without printing configuration or secrets."""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from retailops_ai.knowledge.indexes import MAX_INDEX_BYTES
from retailops_ai.pipelines.indexes import load_index_candidate

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local" / "compose.env"
REQUIRED = {"POSTGRES_PASSWORD", "AI_DB_PASSWORD", "MLFLOW_DB_PASSWORD", "METRICS_TOKEN"}


def project_name() -> str:
    return "retailops_ai_" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]


def environment_file(*, create: bool) -> Path:
    if not LOCAL.exists() and create:
        LOCAL.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(LOCAL, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            for key in sorted(REQUIRED):
                output.write(f"{key}={secrets.token_urlsafe(32)}\n")
    if not LOCAL.is_file() or LOCAL.is_symlink():
        raise ValueError("local_stack_not_initialized")
    if LOCAL.stat().st_mode & 0o077:
        raise ValueError("local_stack_permissions_too_open")
    values: dict[str, str] = {}
    for line in LOCAL.read_text().splitlines():
        key, sep, value = line.partition("=")
        if (
            not sep
            or key not in REQUIRED
            or key in values
            or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value)
        ):
            raise ValueError("invalid_local_stack_configuration")
        values[key] = value
    if set(values) != REQUIRED or len(set(values.values())) != len(values):
        raise ValueError("invalid_local_stack_configuration")
    return LOCAL


def compose_command(env_file: Path, *args: str, stdin: str | None = None) -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker_unavailable")
    command = [
        docker,
        "compose",
        "-p",
        project_name(),
        "--env-file",
        str(env_file),
        "-f",
        str(ROOT / "compose.yaml"),
        *args,
    ]
    result = subprocess.run(
        command, cwd=ROOT, input=stdin, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise RuntimeError("compose_step_failed")
    print("compose_step_passed: " + " ".join(args[:3]), flush=True)
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init", "up", "down", "config", "rag-store"))
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            environment_file(create=True)
            print("local_stack_initialized")
            return 0
        env_file = environment_file(create=args.command in {"up", "config"})
        if args.command == "rag-store":
            if args.candidate is None:
                raise ValueError("candidate_required")
            candidate = load_index_candidate(args.candidate)
            raw = candidate.model_dump_json()
            if len(raw.encode()) > MAX_INDEX_BYTES or candidate.manifest.environment != "local":
                raise ValueError("invalid_compose_candidate")
            result = compose_command(
                env_file,
                "run",
                "--rm",
                "-T",
                "api-migrate",
                "retailops-ai",
                "index-store",
                "--candidate",
                "-",
                stdin=raw,
            )
            summary = json.loads(result)
            if (
                summary.get("status") not in {"stored", "already_present"}
                or summary.get("index_id") != candidate.manifest.index_id
            ):
                raise RuntimeError("invalid_index_storage_result")
            print(
                json.dumps(
                    {
                        "status": summary["status"],
                        "index_id": candidate.manifest.index_id,
                        "chunks": candidate.manifest.chunk_count,
                        "embeddings": candidate.manifest.embedding_count,
                    }
                )
            )
        elif args.command == "config":
            compose_command(env_file, "config", "--quiet")
        elif args.command == "down":
            compose_command(env_file, "down")
        else:
            compose_command(env_file, "up", "-d", "--wait", "db")
            compose_command(env_file, "build", "api", "mlflow")
            compose_command(env_file, "run", "--rm", "api-migrate")
            compose_command(env_file, "run", "--rm", "mlflow-migrate")
            compose_command(env_file, "up", "-d", "--wait", "api", "mlflow")
    except (OSError, ValueError, RuntimeError):
        print('{"error":"local_stack_operation_failed"}', file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
