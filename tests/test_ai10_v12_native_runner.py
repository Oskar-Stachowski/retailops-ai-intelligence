"""A native runner refuses local execution and preserves existing unrelated evidence."""

import importlib.util
import json
import subprocess
import venv
from pathlib import Path

import pytest

from retailops_ai.adapters.git_documents import CorpusError


def runner(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/check_ai10_v12_native.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("ai10_v12_native_owned_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def arguments(tmp_path):
    return [
        "check_ai10_v12_native.py",
        *[
            argument
            for name in ("original", "parents", "output", "tests", "secret-scan", "consumer-root")
            for argument in ("--" + name, str(tmp_path / name))
        ],
        "--image",
        "explicit-fixture-image",
    ]


def test_local_execution_is_refused_before_docker_or_filesystem_mutations(tmp_path, monkeypatch):
    module = runner(monkeypatch)
    monkeypatch.setattr(module.sys, "argv", arguments(tmp_path))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("local Docker must not be contacted")

    monkeypatch.setattr(module, "docker", forbidden)
    assert module.main() == 1
    assert not list(tmp_path.iterdir())


def test_early_refusal_does_not_overwrite_another_invocations_completed_proof(
    tmp_path, monkeypatch
):
    module = runner(monkeypatch)
    output = tmp_path / "output"
    output.mkdir()
    path = output / "acceptance.json"
    original = b'{"status":"passed","owner":"another-invocation"}'
    path.write_bytes(original)
    monkeypatch.setattr(module.sys, "argv", arguments(tmp_path))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert module.main() == 1
    assert path.read_bytes() == original


def test_preflight_accepts_actual_application_contract_without_database_or_docker(monkeypatch):
    module = runner(monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("configuration preflight must not contact Docker")

    monkeypatch.setattr(module, "docker", forbidden)
    module.preflight_application_database()


@pytest.mark.parametrize("resolve_interpreter", [False, True])
def test_source_probe_uses_actual_venv_even_when_python_is_a_symlink(
    tmp_path, monkeypatch, resolve_interpreter
):
    module = runner(monkeypatch)
    root = tmp_path / "source"
    environment = root / "services/api/.venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    python = module.source_consumer_python(root)
    assert python.is_symlink()
    # The real installed pytest is available to both processes. The guard must
    # still reject the base interpreter because its prefix is outside Source.
    monkeypatch.setenv("PYTHONPATH", str(Path(pytest.__file__).parent.parent))
    probe = subprocess.run(
        [str(python), "-c", "import sys; print(sys.prefix)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(probe.stdout.strip()).resolve() == environment.resolve()
    if resolve_interpreter:
        monkeypatch.setattr(module, "source_consumer_python", lambda root: python.resolve())
        with pytest.raises(ValueError, match="ai10_v12_source_python_environment"):
            module.preflight_source_python(root)
    else:
        module.preflight_source_python(root)


def test_missing_pytest_is_bounded_startup_evidence_without_private_stderr(tmp_path, monkeypatch):
    module = runner(monkeypatch)
    log = tmp_path / "private.log"
    log.write_text("/private/python: No module named pytest\nBearer PRIVATE-CREDENTIAL\n")
    report = module.source_failure_details(tmp_path / "absent.xml", log)
    assert report["startup_failure_category"] == "pytest_missing_from_child_interpreter"
    assert "PRIVATE-CREDENTIAL" not in json.dumps(report)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://v12_test:fixture@127.0.0.1:1/v12_test",
        "postgresql+psycopg://ai_app:fixture@127.0.0.1:1/retailops",
        "postgresql+psycopg://source_app:fixture@127.0.0.1:1/retailops_ai",
    ],
)
def test_preflight_rejects_wrong_user_or_database_through_actual_application_guard(
    monkeypatch, url
):
    module = runner(monkeypatch)
    monkeypatch.setattr(module, "original_database_url", lambda *args: url)
    with pytest.raises(CorpusError, match="isolated_ai_database_required"):
        module.preflight_application_database()


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT document FROM ai.intelligence_outbox WHERE event_id=:id",
        "INSERT INTO ai.model_intelligence_outbox(event_id) VALUES (:id)",
        "INSERT INTO ai.intelligence_outbox_backup(event_id) VALUES (:id)",
    ],
)
def test_fault_hook_does_not_match_reads_or_another_table(monkeypatch, statement):
    runner(monkeypatch).fail_outbox_insert(None, None, statement)


def test_fault_hook_matches_actual_production_emitter_sql(monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy.dialects import postgresql

    from retailops_ai.intelligence_events import outbox

    module = runner(monkeypatch)
    record = SimpleNamespace(
        event_id="explicit-mechanics-event", partition_key="fixture-key", topic="fixture-topic"
    )
    record.model_dump_json = lambda: '{"explicit_mechanics_fixture":true}'
    monkeypatch.setattr(outbox, "forecast_events", lambda *args: [record])
    statements = []

    class Connection:
        def execute(self, statement, parameters):
            sql = str(statement.compile(dialect=postgresql.dialect()))
            statements.append(sql)
            module.fail_outbox_insert(None, None, sql)

    output = SimpleNamespace(artifact_id="explicit-mechanics-publication", environment="test")
    with pytest.raises(RuntimeError, match="^ai10_v12_controlled_outbox_insert_failure$"):
        outbox.enqueue_forecasts(Connection(), output, None, None)
    assert len(statements) == 1
    assert statements[0].startswith("\nINSERT")


@pytest.mark.parametrize("prefix", ["tests/", "services/api/tests/"])
def test_source_failure_preserves_actual_pytest_identity_without_raw_output(
    monkeypatch, tmp_path, prefix
):
    module = runner(monkeypatch)
    log = tmp_path / "private.log"
    test = "test_original_v12_sql_outbox_complete_api_and_browser"
    log.write_text(
        f"FAILED {prefix}test_native_forecast_output_durability.py::{test}\n"
        "Authorization: Bearer PRIVATE-CREDENTIAL\n"
    )
    report = module.source_failure_details(tmp_path / "absent.xml", log)
    assert report == {
        "junit_status": "missing",
        "failed_tests": ["tests/test_native_forecast_output_durability.py::" + test],
        "delivery_failure_categories": [],
    }
    assert "PRIVATE-CREDENTIAL" not in json.dumps(report)


def test_source_junit_preserves_failure_location_and_counts_without_private_message(
    monkeypatch, tmp_path
):
    module = runner(monkeypatch)
    junit = tmp_path / "tests.xml"
    junit.write_text("""<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0">
<testcase name="test_original_v12_sql_outbox_complete_api_and_browser">
<failure type="AssertionError" message="Bearer PRIVATE-CREDENTIAL">
tests/test_native_forecast_output_durability.py:213: AssertionError
postgresql://private-user:PRIVATE-CREDENTIAL@127.0.0.1/private-db
</failure></testcase></testsuite></testsuites>""")
    report = module.source_failure_details(junit, tmp_path / "absent.log")
    assert report["test_counts"] == dict(tests=1, failures=1, errors=0, skipped=0)
    assert report["failures"] == [
        dict(
            kind="failure",
            test="test_original_v12_sql_outbox_complete_api_and_browser",
            exception_type="AssertionError",
            test_locations=[dict(path="tests/test_native_forecast_output_durability.py", line=213)],
        )
    ]
    assert "PRIVATE-CREDENTIAL" not in json.dumps(report)
    assert "postgresql" not in json.dumps(report)


@pytest.mark.parametrize(
    "content", ["invalid XML", '<testsuites><testsuite tests="-1"/></testsuites>']
)
def test_invalid_source_junit_cannot_become_passed_evidence(monkeypatch, tmp_path, content):
    junit = tmp_path / "tests.xml"
    junit.write_text(content)
    assert runner(monkeypatch).source_failure_details(junit, tmp_path / "absent.log") == {
        "junit_status": "invalid",
        "failed_tests": [],
    }


@pytest.mark.parametrize(
    "error",
    [
        ValueError("postgresql://private-user:PRIVATE-CREDENTIAL@127.0.0.1/private-db"),
        RuntimeError("Authorization: Bearer PRIVATE-CREDENTIAL"),
    ],
)
def test_delivery_failure_summary_omits_unknown_private_messages(monkeypatch, error):
    runner(monkeypatch)
    from deliver_ai10_v12_native import failure_summary

    report = failure_summary(error)
    assert report["status"] == "failed"
    assert report["category"] == "ai10_v12_original_outbox_delivery"
    assert "PRIVATE-CREDENTIAL" not in json.dumps(report)


def test_delivery_failure_summary_retains_exact_original_census_guard(monkeypatch):
    runner(monkeypatch)
    from deliver_ai10_v12_native import failure_summary

    error = ValueError("ai10_v12_delivery_complete_original_pending_census")
    assert failure_summary(error) == dict(
        status="failed",
        exception_type="ValueError",
        category="ai10_v12_delivery_complete_original_pending_census",
    )
