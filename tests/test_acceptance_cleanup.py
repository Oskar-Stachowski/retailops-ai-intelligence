"""Disposable tests must release their images without deleting unrelated resources."""

import fnmatch
import importlib
import json
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_forecast_publication as runner  # noqa: E402
import lifecycle_store as combined  # noqa: E402
import local_stack as stack  # noqa: E402
import mlflow_store as store  # noqa: E402

from retailops_ai.forecast_jobs.read_progress import read_progress, safe_read_progress  # noqa: E402

PROJECT = "retailops_ai_queue_1234567890"
SMOKES = (
    ("check_forecast_queue", "queue"),
    ("check_forecast_input_store", "inputs"),
    ("check_forecast_publication", "outputs"),
    ("check_forecast_read", "read"),
    ("check_model_catalog", "catalog"),
    ("check_evaluations", "evaluations"),
    ("check_model_lifecycle", "lifecycle"),
    ("check_lifecycle_store", "store_source"),
    ("check_mlflow_store", "restore"),
)


class DockerState:
    def __init__(self) -> None:
        self.images: dict[str, dict[str, str]] = {}
        self.oneoffs: dict[str, tuple[str, str]] = {}
        self.commands: list[list[str]] = []
        self.fail_build = False
        self.fail_remove = False

    def build(self, project: str, service: str = "api") -> str:
        tag = f"{project}-{service}:local"
        self.images[tag] = {
            "retailops.ai.build_project": project,
            "retailops.ai.build_service": service,
        }
        return tag

    def run(self, command: list[str], **_: Any) -> bytes:
        self.commands.append(command)
        if command[1:3] == ["image", "ls"]:
            pattern = command[command.index("--filter") + 1].removeprefix("reference=")
            return "\n".join(
                tag for tag in self.images if fnmatch.fnmatchcase(tag, pattern)
            ).encode()
        if command[1:3] == ["image", "inspect"]:
            return json.dumps(self.images[command[-1]]).encode()
        if command[1:3] == ["image", "rm"]:
            assert command[3] == "--no-prune"
            assert "--force" not in command and "-f" not in command
            if self.fail_remove:
                raise ValueError("image_still_in_use")
            for tag in command[4:]:
                if any(image == tag for _, image in self.oneoffs.values()):
                    raise ValueError("oneoff_image_still_in_use")
                del self.images[tag]
        if "down" in command:
            project = command[command.index("-p") + 1]
            # Compose excludes one-offs by default and inactive-profile services
            # are not orphans. Both settings are needed for migration one-offs.
            if "--remove-orphans" in command and "--profile" in command:
                if command[command.index("--profile") + 1] == "maintenance":
                    self.oneoffs = {
                        name: (owner, image)
                        for name, (owner, image) in self.oneoffs.items()
                        if owner != project
                    }
        if "build" in command:
            project = command[command.index("-p") + 1]
            self.build(project)
            if self.fail_build:
                raise ValueError("partial_build_failed")
        return b""


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DockerState:
    state = DockerState()
    monkeypatch.setattr(store, "checked_run", state.run)
    monkeypatch.setattr(
        store, "compose", lambda project, *args: ["docker", "compose", "-p", project, *args]
    )
    monkeypatch.setattr(stack, "environment_file", lambda **_: tmp_path / "compose.env")
    monkeypatch.setattr(stack, "main", lambda _: 0)
    monkeypatch.setattr(combined, "MAINTENANCE", tmp_path / "maintenance.json")
    monkeypatch.setattr(sys, "argv", ["smoke"])
    monkeypatch.setattr(uuid, "uuid4", lambda: SimpleNamespace(hex="1234567890" * 3 + "12"))
    return state


def test_cleanup_removes_only_owned_tags_and_is_repeatable(docker: DockerState) -> None:
    api = docker.build(PROJECT)
    docker.build(PROJECT, "mlflow")
    # The same image may have a tag belonging to another workflow.
    docker.images["saved-debug-copy:local"] = docker.images[api]
    foreign = docker.build("retailops_ai_queue_abcdefghij")
    docker.images["postgres:16"] = {}
    expected = {"saved-debug-copy:local", foreign, "postgres:16"}

    store.cleanup_test_stacks(PROJECT)
    store.cleanup_test_stacks(PROJECT)

    assert set(docker.images) == expected
    down = next(i for i, command in enumerate(docker.commands) if "down" in command)
    remove = next(i for i, command in enumerate(docker.commands) if "rm" in command)
    assert down < remove
    assert not any("prune" in command or "--rmi" in command for command in docker.commands)


@pytest.mark.parametrize("service", ["api", "mlflow"])
def test_cleanup_removes_timed_out_migration_oneoff_before_image(docker: DockerState, service):
    image = docker.build(PROJECT, service)
    foreign = "retailops_ai_queue_abcdef1234"
    foreign_image = docker.build(foreign, service)
    docker.oneoffs = {
        "interrupted-migration": (PROJECT, image),
        "other-session": (foreign, foreign_image),
    }

    store.cleanup_test_stacks(PROJECT)

    assert docker.oneoffs == {"other-session": (foreign, foreign_image)}
    assert set(docker.images) == {foreign_image}


@pytest.mark.parametrize("project", ["retailops_ai_1234567890", "other", PROJECT + "extra"])
def test_cleanup_rejects_persistent_or_invalid_project(docker: DockerState, project: str) -> None:
    with pytest.raises(ValueError, match="test_stack_cleanup_failed"):
        store.cleanup_test_stacks(project)
    assert not docker.commands


@pytest.mark.parametrize("label", ["retailops.ai.build_project", "retailops.ai.build_service"])
def test_wrong_image_owner_prevents_deletion(docker: DockerState, label: str) -> None:
    tag = docker.build(PROJECT)
    docker.images[tag][label] = "unrelated"
    with pytest.raises(ValueError, match="test_stack_cleanup_failed"):
        store.cleanup_test_stacks(PROJECT)
    assert tag in docker.images
    assert not any("down" in command or "rm" in command for command in docker.commands)


def test_automatic_compose_labels_do_not_replace_explicit_build_ownership(docker: DockerState):
    tag = docker.build(PROJECT)
    docker.images[tag] = {
        "com.docker.compose.project": PROJECT,
        "com.docker.compose.service": "api",
    }
    with pytest.raises(ValueError, match="test_stack_cleanup_failed"):
        store.cleanup_test_stacks(PROJECT)
    assert tag in docker.images
    assert not any("down" in command or "rm" in command for command in docker.commands)


def test_cleanup_attempts_other_owned_projects_and_reports_failure(docker: DockerState) -> None:
    other = "retailops_ai_store_target_1234567890"
    protected = docker.build(PROJECT)
    docker.images[protected]["retailops.ai.build_project"] = "unrelated"
    removed = docker.build(other)
    with pytest.raises(ValueError, match="test_stack_cleanup_failed"):
        store.cleanup_test_stacks(PROJECT, other)
    assert protected in docker.images and removed not in docker.images


@pytest.mark.parametrize("module,prefix", SMOKES)
def test_smoke_preserves_preexisting_images_before_claiming_ownership(
    docker: DockerState, module: str, prefix: str
) -> None:
    smoke = importlib.import_module(module)
    project = f"retailops_ai_{prefix}_1234567890"
    tag = docker.build(project)
    original_compose = store.compose

    assert smoke.main() != 0

    assert tag in docker.images
    assert not any(
        "down" in command or "rm" in command or "build" in command for command in docker.commands
    )
    assert store.compose is original_compose


@pytest.mark.parametrize("module,prefix", SMOKES)
def test_smoke_cleans_images_after_failure_without_any_containers(
    docker: DockerState, monkeypatch: pytest.MonkeyPatch, module: str, prefix: str
) -> None:
    smoke = importlib.import_module(module)
    original_compose = store.compose
    docker.fail_build = True
    if prefix == "restore":
        # Abort after target ownership is established, as a partial restore would.
        def fail_api(*_: Any, **__: Any) -> None:
            docker.build("retailops_ai_restore_1234567890", "mlflow")
            raise ValueError("restore_failed")

        monkeypatch.setattr(smoke, "api", fail_api)

    assert smoke.main() != 0

    assert not docker.images
    assert any("rm" in command for command in docker.commands)
    assert store.compose is original_compose


def test_cleanup_error_is_visible_and_compose_override_is_restored(docker: DockerState) -> None:
    import check_forecast_queue as smoke

    original_compose = store.compose
    docker.fail_build = True
    docker.fail_remove = True
    with pytest.raises(ValueError, match="test_stack_cleanup_failed"):
        smoke.main()
    assert docker.images  # A conflicting image is not forcibly removed.
    assert store.compose is original_compose


def prepare(monkeypatch, tmp_path, docker: DockerState):
    report = tmp_path / "forecast-read.json"
    report.write_text('{"status":"passed","previous_report":true}')
    monkeypatch.setattr(sys, "argv", ["smoke", "--report", str(report)])
    original = docker.run

    def checked(command, **kwargs):
        if command[1:3] == ["image", "inspect"] and "{{.Id}}" in command:
            return ("sha256:" + "a" * 64).encode()
        if "--inspect" in command:
            return b"{}"
        return original(command, **kwargs)

    monkeypatch.setattr(runner.store, "checked_run", checked)
    return report


def marker(stage="outside_history", completed=12, elapsed_ms=410000):
    return json.dumps(
        dict(forecast_read_progress=stage, completed=completed, elapsed_ms=elapsed_ms)
    ).encode()


def test_timeout_keeps_only_safe_progress_and_original_reason(
    docker, monkeypatch, tmp_path, capsys
):
    report = prepare(monkeypatch, tmp_path, docker)

    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 420
        assert json.loads(report.read_text())["status"] == "running"
        raw = b"private-credential-fixture\n" + marker() + b"\n"
        raw += marker(stage="private-credential-fixture")
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], stderr=raw)

    monkeypatch.setattr(runner.subprocess, "run", timeout)
    assert runner.main(read=True) == 2
    stored = json.loads(report.read_text())
    assert stored["status"] == "failed"
    assert stored["reason"] == "TimeoutExpired" and stored["phase"] == "read_acceptance"
    assert stored["cleanup_status"] == "passed"
    output = capsys.readouterr().out
    assert '"forecast_read_progress": "outside_history"' in output
    assert "private-credential-fixture" not in output + report.read_text()
    assert "previous_report" not in stored and '"status": "passed"' not in output
    assert not docker.images


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_success_is_published_only_after_cleanup(
    docker, monkeypatch, tmp_path, capsys, cleanup_fails
):
    report = prepare(monkeypatch, tmp_path, docker)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=b'{"checks":[]}', stderr=marker("final_snapshot")
        ),
    )
    docker.fail_remove = cleanup_fails
    if cleanup_fails:
        with pytest.raises(ValueError, match="test_stack_cleanup_failed"):
            runner.main(read=True)
    else:
        assert runner.main(read=True) == 0
    stored = json.loads(report.read_text())
    assert stored["status"] == ("failed" if cleanup_fails else "passed")
    assert stored["cleanup_status"] == ("failed" if cleanup_fails else "passed")
    assert ('"status": "passed"' in capsys.readouterr().out) is not cleanup_fails
    assert "previous_report" not in stored


def test_input_failure_cannot_leave_previous_passing_report(docker, monkeypatch, tmp_path):
    report = prepare(monkeypatch, tmp_path, docker)
    monkeypatch.setattr(runner.stack, "ROOT", tmp_path)
    assert runner.main(read=True) == 2
    stored = json.loads(report.read_text())
    assert stored["status"] == "failed" and stored["phase"] == "prepare_inputs"
    assert stored["reason"] == "FileNotFoundError"
    assert "previous_report" not in stored and not docker.commands


@pytest.mark.parametrize(
    "raw",
    [
        b"private-output-fixture",
        b"\xff",
        b"[]",
        b"null",
        marker(stage="unknown_fixture"),
        marker(completed=True),
        marker(completed=34),
        marker(completed=-1),
        marker(elapsed_ms=-1),
        marker(elapsed_ms=3_600_001),
        marker(elapsed_ms=True),
        marker(elapsed_ms=1.5),
        b'{"forecast_read_progress": [], "completed": 0, "elapsed_ms": 1}',
        marker()[:-1] + b', "token": "private-fixture"}',
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_progress_rejects_unknown_schema_values_and_private_text(raw):
    assert safe_read_progress(raw) == []


def test_progress_bounds_output_even_for_repeated_valid_child_lines():
    raw = b"\n".join(marker(completed=i % 34) for i in range(1000))
    records = safe_read_progress(raw)
    assert len(records) == 32
    assert records[-1] == json.loads(marker(completed=999 % 34))


def test_timeout_and_cleanup_failure_preserve_both_failures(docker, monkeypatch, tmp_path, capsys):
    report = prepare(monkeypatch, tmp_path, docker)
    docker.fail_remove = True

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], stderr=marker())

    monkeypatch.setattr(runner.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="test_stack_cleanup_failed"):
        runner.main(read=True)
    stored = json.loads(report.read_text())
    assert stored["status"] == "failed" and stored["cleanup_status"] == "failed"
    assert stored["reason"] == "TimeoutExpired" and stored["phase"] == "read_acceptance"
    assert '"status": "passed"' not in capsys.readouterr().out


@pytest.mark.parametrize("stage", ["inspect", "cleanup"])
def test_interruption_after_child_success_never_publishes_pass(
    docker, monkeypatch, tmp_path, capsys, stage
):
    report = prepare(monkeypatch, tmp_path, docker)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=b'{"status":"passed","checks":[]}', stderr=b""
        ),
    )
    checked = runner.store.checked_run

    def interrupted(command, **kwargs):
        if (stage == "inspect" and "--inspect" in command) or (
            stage == "cleanup" and "down" in command
        ):
            raise KeyboardInterrupt
        return checked(command, **kwargs)

    monkeypatch.setattr(runner.store, "checked_run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        runner.main(read=True)
    stored = json.loads(report.read_text())
    assert stored["status"] == "failed"
    assert '"status": "passed"' not in capsys.readouterr().out


def test_progress_emitter_uses_stderr_and_flushes_fixed_fields(monkeypatch, capsys):
    from retailops_ai.forecast_jobs import read_progress as module

    monkeypatch.setattr(module.time, "monotonic", lambda: 10.25)
    read_progress("inputs_registered", 10)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert safe_read_progress(captured.err.encode()) == [
        dict(forecast_read_progress="inputs_registered", completed=0, elapsed_ms=250)
    ]
