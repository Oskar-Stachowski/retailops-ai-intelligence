"""A native runner refuses local execution and preserves existing unrelated evidence."""

import importlib.util
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
