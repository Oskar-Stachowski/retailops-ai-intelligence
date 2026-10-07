"""Run the pinned independent Source reader before the original owned AI database is removed."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.source_snapshot.files import read_json

ROOT = Path(__file__).resolve().parents[1]


def run_source_model_read(
    *, engine: Engine, database_url: str, output: Path, consumer: Path, work: Path, kind: str
) -> dict[str, Any]:
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or kind not in {"anomaly_detected", "stockout_risk_scored"}
    ):
        raise ValueError("ai10_model_consumer_owned_runner_required")
    consumer = consumer.resolve()
    git = shutil.which("git")
    if git is None:
        raise ValueError("ai10_model_consumer_git_required")
    pin = read_json(ROOT, "docs/reference/ai10-native-output-consumer.json")
    source_head = subprocess.check_output(  # noqa: S603 - resolved git, fixed read-only arguments
        [git, "rev-parse", "HEAD"], cwd=consumer, text=True
    ).strip()
    if source_head != pin["commit"] or any(
        hashlib.sha256((consumer / name).read_bytes()).hexdigest() != digest
        for name, digest in pin["sha256"].items()
    ):
        raise ValueError("ai10_model_consumer_exact_original_revision")
    acceptance = read_json(output, "acceptance.json")
    native = acceptance["acceptance"] if kind == "anomaly_detected" else acceptance
    census_id = native["native_outbox_census_id"]
    receipt = read_json(output, "native-outbox/" + census_id + "/receipt.json")
    members = receipt["members"]
    correlations = {value["correlation_id"] for value in members}
    if (
        not 1 <= len(members) <= 1400
        or len(correlations) != 1
        or any(value["event_type"] != kind for value in members)
    ):
        raise ValueError("ai10_model_consumer_complete_native_census")
    owner = uuid.uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ai.service_metadata(name,value) VALUES ('ai10_native_model_owner',CAST(:owner AS jsonb))"
            ),
            dict(owner=json.dumps(owner)),
        )
    control = work / ("original-model-control-" + owner + ".json")
    document = dict(
        owner=owner,
        database_url=database_url,
        commit=os.environ["GITHUB_SHA"],
        workflow_run_id=int(os.environ["GITHUB_RUN_ID"]),
        kind=kind,
        rows=len(members),
        census_id=census_id,
        correlation_id=next(iter(correlations)),
        census_sha256=canonical_sha256(
            {value["event_id"]: value["event_sha256"] for value in members}
        ),
    )
    fd = os.open(control, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical_bytes(document))
    env = {
        **os.environ,
        "PYTHONPATH": str(consumer) + os.pathsep + str(consumer / "services/api"),
        "REQUIRE_BROKER_TESTS": "1",
        "REQUIRE_AI10_NATIVE_MODEL_READ": "1",
        "REQUIRE_AI10_NATIVE_BROWSER": "1",
        "AI10_NATIVE_MODEL_KIND": kind,
        "AI10_NATIVE_ANOMALY_OUTPUT"
        if kind == "anomaly_detected"
        else "AI10_NATIVE_STOCKOUT_OUTPUT": str(output.resolve()),
        "AI10_NATIVE_PRODUCER_COMMIT": os.environ["GITHUB_SHA"],
        "AI10_NATIVE_READ_REPORT": str((output / "source-native-read.json").resolve()),
        "AI10_MODEL_ORIGINAL_DATABASE_CONTROL": str(control),
        "AI10_NATIVE_DELIVERY_PYTHON": str(ROOT / "tools/intelligence-delivery/.venv/bin/python"),
        "AI10_NATIVE_DELIVERY_SCRIPT": str(ROOT / "scripts/deliver_ai10_model_native.py"),
    }
    try:
        with (work / "model-source-consumer.log").open("wb") as log:
            result = subprocess.run(  # noqa: S603 - fixed test in the byte-verified pinned checkout
                [
                    str(consumer / "services/api/.venv/bin/python"),
                    "-m",
                    "pytest",
                    "-q",
                    "-x",
                    "services/api/tests/test_native_intelligence_output_durability.py",
                    "--junitxml=" + str(work / "model-source-tests.xml"),
                ],
                cwd=consumer,
                env=env,
                stdout=log,
                stderr=log,
                timeout=1800,
                check=False,
            )
        if result.returncode:
            private_log = (work / "model-source-consumer.log").read_text()
            print(
                json.dumps(
                    dict(
                        category="ai10_model_original_source_consumer_failed",
                        tests=re.findall(
                            r"FAILED (services/api/tests/[a-zA-Z0-9_/.]+::[a-zA-Z0-9_]+)",
                            private_log,
                        ),
                        locations=re.findall(
                            r"(services/api/tests/[a-zA-Z0-9_/.]+\.py):([0-9]{1,5}): ([A-Za-z_][A-Za-z0-9_.]{0,80})",
                            private_log,
                        ),
                        publisher_failure_categories=re.findall(
                            r'"reason": "(ai10_model_[a-z_]{1,100})"',
                            private_log,
                        ),
                    )
                )
            )
            raise ValueError("ai10_model_original_source_consumer_failed")
        report = read_json(output, "source-native-read.json")
        if not (
            report["status"] == "passed"
            and report["source_commit"] == pin["commit"]
            and report["original_AI_database_publisher_attested"] is True
            and report["native_payloads_unchanged"] is True
            and report["rows"] == len(members)
            and report["browser"]["status"] == "passed"
        ):
            raise ValueError("ai10_model_complete_independent_source_attestation")
        shutil.copyfile(work / "model-source-tests.xml", output / "source-native-tests.xml")
        (output / "source-native-tests.xml").chmod(0o600)
        return report
    except Exception:
        acceptance.update(
            status="failed", failure_category="ai10_model_original_source_consumer_failed"
        )
        (output / "acceptance.json").write_bytes(canonical_bytes(acceptance) + b"\n")
        raise
    finally:
        control.unlink()
