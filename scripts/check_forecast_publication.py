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


def main(*, read: bool = False, catalog: bool = False) -> int:
    if read and catalog:
        raise ValueError("ambiguous_acceptance_mode")
    mode = "catalog" if catalog else "read" if read else "publication"
    module = {
        "catalog": "retailops_ai.model_lifecycle.read_acceptance",
        "read": "retailops_ai.forecast_jobs.read_acceptance",
        "publication": "retailops_ai.forecast_jobs.publication_acceptance",
    }[mode]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        default=stack.ROOT
        / (
            "reports/model-catalog.json"
            if catalog
            else "reports/forecast-read.json"
            if read
            else "reports/forecast-publication.json"
        ),
    )
    args = parser.parse_args()
    raw = (stack.ROOT / "contracts/forecast_jobs/v1/fixture/inputs.json").read_bytes()
    strict_json(raw)
    inputs = PreparedInputs.model_validate_json(raw)
    project = (
        "retailops_ai_catalog_"
        if catalog
        else "retailops_ai_read_"
        if read
        else "retailops_ai_outputs_"
    ) + uuid.uuid4().hex[:10]
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
        store.require_fresh_test_images(project)
        owned = True
        phase = "build_api"
        store.checked_run(store.compose(project, "build", "api"))
        phase = "start_database"
        store.checked_run(store.compose(project, "up", "-d", "--wait", "db"))
        phase = "migrate_database"
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
            module,
        )
        phase = mode + "_acceptance"
        result = subprocess.run(  # noqa: S603 - structured disposable acceptance with private stdin
            command,
            cwd=stack.ROOT,
            input=inputs.model_dump_json().encode(),
            capture_output=True,
            timeout=240 if read or catalog else 180,
            check=False,
        )
        if result.returncode:
            if read:
                for line in result.stdout.decode(errors="replace").splitlines():
                    if re.fullmatch(r'\{"read_sqlstate": "[A-Z0-9]{5}"\}', line):
                        print(line)
            markers = re.findall(
                rb"(?:ValueError|RuntimeError): ([a-z][a-z_]{0,100})\s*$",
                result.stderr,
                re.MULTILINE,
            )
            print(json.dumps({"acceptance_errors": [v.decode() for v in markers]}))
            raise ValueError(mode + "_acceptance_failed")
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
            "sigkill_database_restart_preserves_scoped_catalog_and_full_publication_state"
            if catalog
            else "sigkill_database_restart_preserves_read_view_predictions_and_full_publication_state"
            if read
            else "sigkill_database_restart_preserves_complete_forecast_partitions_manifests_pins_history_and_heads"
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(
            json.dumps(
                {"status": "passed", "report": str(args.report), "published_forecast_outputs": 0}
            )
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        reason = (
            str(exc)
            if isinstance(exc, ValueError) and re.fullmatch(r"[a-z][a-z_]{1,100}", str(exc))
            else type(exc).__name__
        )
        print(
            json.dumps(
                {
                    "error": "model_catalog_smoke_failed"
                    if catalog
                    else "forecast_read_smoke_failed"
                    if read
                    else "forecast_publication_smoke_failed",
                    "phase": phase,
                    "reason": reason,
                    "project": project,
                }
            )
        )
        return 2
    finally:
        try:
            if owned:
                store.cleanup_test_stacks(project)
        finally:
            store.compose = original


if __name__ == "__main__":
    raise SystemExit(main())
