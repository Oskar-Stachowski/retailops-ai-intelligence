"""Genuine native output evidence must keep the original scientific and serving gates."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_ai10_requalification_uses_original_successful_jobs_without_repeating_evaluation():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ai10-qualified-output.yml").read_text())
    steps = workflow["jobs"]["accept"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "scripts/recover_stockout_final_receipts.py" in commands
    assert "scripts/qualify_stockout_final.py" in commands
    assert (
        ".local/ai10-original-qualified-runtime/.venv/bin/python scripts/qualify_stockout_final.py"
        in commands
    )
    assert "--no-editable" in commands
    original_runtime = next(
        step["with"]
        for step in steps
        if step.get("with", {}).get("path") == ".local/ai10-original-qualified-runtime"
    )
    assert original_runtime["ref"] == "68a3ede16da4bd8b93609c2489514c08e95a7384"
    assert original_runtime["persist-credentials"] is False
    assert "--final-execution-commit 048f7b311b82dbf831718565289a54166cbe7d36" in commands
    assert "--final-run-id 37273978383" in commands
    assert "scripts/run_stockout_final_world.py" not in commands
    assert "scripts/reuse_stockout_final_qualification.py" not in commands
    assert "scripts/check_stockout_final_acceptance.py" in commands
    assert (
        "--tests acceptance-proofs/tests.xml --secret-scan acceptance-proofs/secrets.json"
        in commands
    )
    original = yaml.safe_load((ROOT / ".github/workflows/ai08-final-acceptance.yml").read_text())
    model_tests = next(s["run"] for s in steps if "--junitxml=" in s.get("run", ""))
    original_tests = next(
        s["run"] for s in original["jobs"]["accept"]["steps"] if "--junitxml=" in s.get("run", "")
    )
    for token in original_tests.split():
        if token.startswith("tests/"):
            assert token in model_tests
    scanner = next(
        index for index, step in enumerate(steps) if step.get("uses", "").startswith("gitleaks/")
    )
    proof = next(index for index, step in enumerate(steps) if "secrets.json" in step.get("run", ""))
    assert scanner < proof
    assert all(not step.get("continue-on-error") for step in steps)
    assert (workflow.get("on") or workflow[True])["push"]["branches"] == ["ai/10-ready"]
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    pin = json.loads((ROOT / "docs/reference/ai10-native-output-consumer.json").read_text())
    consumer = next(
        step["with"]
        for step in steps
        if step.get("with", {}).get("repository") == pin["repository"]
    )
    assert consumer["ref"] == pin["commit"]
    assert consumer["persist-credentials"] is False
    receiver = next(
        step
        for step in steps
        if "test_native_intelligence_output_durability.py" in step.get("run", "")
    )
    assert receiver["env"]["REQUIRE_BROKER_TESTS"] == "1"
    assert receiver["env"]["REQUIRE_AI10_NATIVE_MODEL_READ"] == "1"
    assert receiver["env"]["AI10_NATIVE_PRODUCER_COMMIT"] == "${{ github.sha }}"
    assert receiver["env"]["AI10_NATIVE_STOCKOUT_OUTPUT"].endswith("/accepted-model")
