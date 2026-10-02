"""Disposable tests must release their images without deleting unrelated resources."""

import fnmatch
import importlib
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lifecycle_store as combined  # noqa: E402
import local_stack as stack  # noqa: E402
import mlflow_store as store  # noqa: E402

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
                del self.images[tag]
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
