"""A native runner refuses local execution and preserves existing unrelated evidence."""

import importlib.util
from pathlib import Path


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
