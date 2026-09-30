"""Disposable PostgreSQL publication/worker acceptance with explicit synthetic quality stubs."""

import argparse
import json
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lifecycle_store as combined
import local_stack as stack
import mlflow_store as store

from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.security.local import strict_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", type=Path, default=stack.ROOT / "reports/forecast-publication.json"
    )
    args = parser.parse_args()
    raw = (stack.ROOT / "contracts/forecast_jobs/v1/fixture/inputs.json").read_bytes()
    strict_json(raw)
    inputs = PreparedInputs.model_validate_json(raw)
    project = "retailops_ai_outputs_" + uuid.uuid4().hex[:10]
    owned = False
    target = project
    original = store.compose

    def isolated(project: str, *args: str) -> list[str]:
        base = original(project)
        if project == target:
            base.extend(["-f", str(stack.ROOT / "infra/compose-acceptance.yaml")])
        return [*base, *args]

    store.compose = isolated
    report: dict[str, Any] = {
        "checked_at": datetime.now(UTC).isoformat(),
        "project": project,
        "input_origin": "controlled_contract_fixture",
    }
    phase = "preflight"
    try:
        stack.environment_file(create=True)
        combined.require_fresh(project)
        owned = True
        phase = "setup"
        store.checked_run(store.compose(project, "build", "api"))
        store.checked_run(store.compose(project, "up", "-d", "--wait", "db"))
        store.checked_run(store.compose(project, "run", "--rm", "-T", "api-migrate"))
        image_digest = (
            store.checked_run(
                [
                    store.compose(project)[0],
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    project + "-api:local",
                ]
            )
            .decode()
            .strip()
        )
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) is None:
            raise ValueError("publication_runtime_image_digest_missing")
        report["image_digest"] = image_digest
        command = store.compose(
            project,
            "run",
            "--rm",
            "-T",
            "-e",
            "APP_ENV=test",
            "-e",
            "IMAGE_DIGEST=" + image_digest,
            "api-migrate",
            "python",
            "-m",
            "retailops_ai.forecast_jobs.publication_acceptance",
        )
        phase = "publication_acceptance"
        result = subprocess.run(  # noqa: S603 - structured disposable acceptance with private stdin
            command,
            cwd=stack.ROOT,
            input=inputs.model_dump_json().encode(),
            capture_output=True,
            timeout=180,
            check=False,
        )
        if result.returncode:
            markers = re.findall(
                rb"(?:ValueError|RuntimeError): ([a-z][a-z_]{0,100})\s*$",
                result.stderr,
                re.MULTILINE,
            )
            print(json.dumps({"acceptance_errors": [v.decode() for v in markers]}))
            raise ValueError("publication_acceptance_failed")
        report.update(json.loads(result.stdout))
        before = json.loads(store.checked_run([*command, "--inspect"]))
        phase = "database_sigkill_restart"
        store.checked_run(store.compose(project, "kill", "-s", "SIGKILL", "db"))
        store.checked_run(store.compose(project, "up", "-d", "--wait", "db"))
        after = json.loads(store.checked_run([*command, "--inspect"]))
        if before != after:
            raise ValueError("published_output_state_changed_after_restart")
        report.update(after)
        report["checks"].append(
            "sigkill_database_restart_preserves_complete_forecast_partitions_manifests_pins_history_and_heads"
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(
            json.dumps(
                {"status": "passed", "report": str(args.report), "published_forecast_outputs": 0}
            )
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"error": "forecast_publication_smoke_failed", "phase": phase}))
        return 2
    finally:
        if owned:
            store.checked_run(store.compose(project, "down", "-v"))
        store.compose = original


if __name__ == "__main__":
    raise SystemExit(main())
