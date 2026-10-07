import hashlib
import json
from pathlib import Path

import pytest

from retailops_ai.agent.evaluation import evaluate_sync, evaluator_checksum, load_evaluation
from retailops_ai.agent.evaluation_contracts import AgentEvaluationRelease, AgentGoldenSet, Rate
from retailops_ai.agent.graph_config import load_graph_config
from retailops_ai.cli import main
from retailops_ai.data_contracts.identity import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "agent/graph.evaluate.fake.native-sources.v1.json"
GOLDEN = ROOT / "agent/golden.canonical.v1.json"
RELEASE = ROOT / "agent/evaluation-release.fake.native-sources.v1.json"
RAG = ROOT / "knowledge/golden.semantic.v1.json"
LOCK = ROOT / "uv.lock"


def test_frozen_golden_runs_real_graph_with_explicit_denominators_and_no_raw_reports(monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("Offline evaluation attempted network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    config = load_graph_config(CONFIG)
    suite, release = load_evaluation(GOLDEN, RELEASE, config, RAG, LOCK)
    report = evaluate_sync(suite, release, config)
    assert report.status == "passed", report.model_dump_json()
    assert report.metrics["case_pass"].total == 50
    assert report.metrics["critical_cases"].value == 1
    assert report.metrics["citation_correctness"].total == 6
    assert report.metrics["numeric_faithfulness"].total > 0
    assert report.metrics["refusal_correctness"].total == 4
    assert report.unnecessary_tool_calls == 0 and report.estimated_cost_usd == "0"
    assert report.fixture_only and not report.aws_executed and not report.rag_quality_remeasured
    assert report.labels_state == "proposed"
    raw = report.model_dump_json()
    for case in suite.cases:
        assert json.loads(case.request_json)["question"] not in raw
    assert "tool_results" not in raw and "Bearer" not in raw


@pytest.mark.parametrize("field", ["golden", "rag", "lock", "evaluator", "config"])
def test_changed_release_inputs_fail_before_provider_execution(field, tmp_path):
    config = load_graph_config(CONFIG)
    golden, rag, lock, release = GOLDEN, RAG, LOCK, RELEASE
    target = tmp_path / "changed.json"
    if field == "golden":
        value = json.loads(GOLDEN.read_text())
        value["cases"][0]["script"][0]["body"] = "not-json"
        target.write_text(json.dumps(value))
        golden = target
    elif field in {"rag", "lock"}:
        original = RAG if field == "rag" else LOCK
        target.write_bytes(original.read_bytes() + b"\n")
        if field == "rag":
            rag = target
        else:
            lock = target
    else:
        value = json.loads(RELEASE.read_text())
        value["evaluator_sha256" if field == "evaluator" else "graph_config_id"] = (
            "a" * 64 if field == "evaluator" else "agent-graph-config-sha256-" + "a" * 64
        )
        value["release_id"] = "agent-evaluation-release-sha256-" + canonical_sha256(
            {key: val for key, val in value.items() if key != "release_id"}
        )
        target.write_text(json.dumps(value))
        release = target
    with pytest.raises(ValueError, match="agent_evaluation_binding_invalid"):
        load_evaluation(golden, release, config, rag, lock)


def test_labels_are_versioned_bounded_and_bound_to_unchanged_rag_questions():
    suite = AgentGoldenSet.model_validate_json(GOLDEN.read_bytes())
    release = AgentEvaluationRelease.model_validate_json(RELEASE.read_bytes())
    assert release.golden_sha256 == canonical_sha256(suite.model_dump(mode="json"))
    assert release.dependency_lock_sha256 == hashlib.sha256(LOCK.read_bytes()).hexdigest()
    assert release.evaluator_sha256 == evaluator_checksum()
    assert sum(case.rag_case_id is not None for case in suite.cases) == 6
    value = suite.model_dump(mode="json")
    value["thresholds"]["deterministic_rate_min"] = 0.99
    with pytest.raises(ValueError):
        AgentGoldenSet.model_validate_json(json.dumps(value))


def test_changed_oracle_is_reported_as_a_failed_gate_without_rewriting_labels():
    config = load_graph_config(CONFIG)
    suite, release = load_evaluation(GOLDEN, RELEASE, config, RAG, LOCK)
    value = suite.model_dump(mode="json")
    case = next(case for case in value["cases"] if case["case_id"] == "business-sales")
    case["expected"]["answer"]["evidence"][0]["claim"] = "Observed sales=999 unit."
    changed = AgentGoldenSet.model_validate_json(json.dumps(value))
    release_value = release.model_dump(mode="json")
    release_value["golden_sha256"] = canonical_sha256(changed.model_dump(mode="json"))
    release_value["release_id"] = "agent-evaluation-release-sha256-" + canonical_sha256(
        {key: val for key, val in release_value.items() if key != "release_id"}
    )
    changed_release = AgentEvaluationRelease.model_validate_json(json.dumps(release_value))
    report = evaluate_sync(changed, changed_release, config)
    assert report.status == "failed"
    assert report.metrics["case_pass"].passed == 49
    assert report.metrics["numeric_faithfulness"].value < 1
    assert "numeric_faithfulness" in report.failed_gates
    assert changed.cases[0].expected.answer.evidence[0].claim == "Observed sales=999 unit."
    value = suite.model_dump(mode="json")
    value["cases"].append(value["cases"][0])
    with pytest.raises(ValueError):
        AgentGoldenSet.model_validate_json(json.dumps(value))


def test_empty_or_inconsistent_metric_never_reports_a_success_rate():
    assert Rate(passed=0, total=0, value=None).value is None
    with pytest.raises(ValueError):
        Rate(passed=0, total=0, value=1.0)
    with pytest.raises(ValueError):
        Rate(passed=2, total=1, value=1.0)


def test_cli_rejects_missing_binding_without_output_or_sensitive_exception(capsys, tmp_path):
    destination = tmp_path / "report.json"
    assert (
        main(
            [
                "agent-evaluate",
                "--provider",
                "fake",
                "--config",
                str(CONFIG),
                "--golden",
                str(tmp_path / "private-marker"),
                "--release",
                str(RELEASE),
                "--rag-golden",
                str(RAG),
                "--lock",
                str(LOCK),
                "--output",
                str(destination),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.err == "agent_evaluation_failed\n" and captured.out == ""
    assert not destination.exists()
