"""Private cached-image Compose fixture; all commands are restricted to owned UUID projects."""

import base64
import json
import os
import re
import secrets
import shutil
import stat
from pathlib import Path

import lifecycle_store as combined
import local_stack as stack
import mlflow_store as store
import yaml
from check_v12_lifecycle import ROOT, docker

from retailops_ai.source_snapshot.files import checked_directory

OWNER_LABEL = "retailops.ai05.v12.backup_owner"


class FixtureStack:
    def __init__(self, work: Path) -> None:
        checked_directory(work)
        if work.stat().st_uid != os.getuid() or work.stat().st_mode & 0o077:
            raise ValueError("v12_backup_workspace_permissions")
        fd = os.open(work / "control.json", os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("v12_backup_control_permissions")
            self.control = json.loads(stream.read(16384))
        self.work = work
        self.owner: str = self.control["owner"]
        if not isinstance(self.owner, str) or re.fullmatch(r"[0-9a-f]{32}", self.owner) is None:
            raise ValueError("v12_backup_owner_invalid")
        self.projects: dict[str, str] = self.control["projects"]
        expected = {
            role: "retailops_ai_v12_" + role + "_" + self.owner[:10]
            for role in ("source", "target", "failed")
        }
        if self.projects != expected:
            raise ValueError("v12_backup_project_names")
        if set(self.control["images"]) != {"db", "mlflow"} or any(
            not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
            for value in self.control["images"].values()
        ):
            raise ValueError("v12_backup_image_invalid")
        self.executable = shutil.which("docker")
        if self.executable is None:
            raise ValueError("docker_unavailable")

    @staticmethod
    def create(work: Path, owner: str, images: dict[str, str]) -> "FixtureStack":
        work.chmod(0o700)
        combined.write_private(
            work / "control.json",
            dict(
                owner=owner,
                images=images,
                projects={
                    r: "retailops_ai_v12_" + r + "_" + owner[:10]
                    for r in ("source", "target", "failed")
                },
            ),
        )
        with (work / "compose.env").open("x") as stream:
            for name in sorted(stack.REQUIRED):
                stream.write(name + "=" + secrets.token_hex(24) + "\n")
        (work / "compose.env").chmod(0o600)
        spec = yaml.safe_load((ROOT / "compose.yaml").read_bytes())
        # The actual roles/init, PostgreSQL backend URI, artifact volume and MLflow
        # settings are retained; host ASGI tests replace only the API container.
        spec["services"] = {k: spec["services"][k] for k in ("db", "mlflow")}
        for role, service in spec["services"].items():
            service.pop("build", None)
            service["image"] = images[role]
            service["pull_policy"] = "never"
            service["restart"] = "no"
            service["mem_limit"] = "512m" if role == "db" else "1536m"
            service["cpus"] = 1
            service["labels"] = {OWNER_LABEL: owner}
            service["networks"] = ["fixture"]
            service["ports"] = ["127.0.0.1::" + ("5432" if role == "db" else "5000")]
        spec["services"]["api"] = dict(
            image=images["mlflow"],
            pull_policy="never",
            profiles=["host_asgi_only"],
            labels={OWNER_LABEL: owner},
            command=["python", "-c", "raise SystemExit(1)"],
            networks=["fixture"],
        )
        spec["services"]["db"]["volumes"][1] = (
            str(ROOT / "infra/postgres/init.sh")
            + ":/docker-entrypoint-initdb.d/10-retailops-ai.sh:ro"
        )
        spec["networks"] = {"fixture": dict(labels={OWNER_LABEL: owner})}
        spec["volumes"] = {k: dict(labels={OWNER_LABEL: owner}) for k in spec["volumes"]}
        (work / "compose.yaml").write_text(yaml.safe_dump(spec))
        (work / "compose.yaml").chmod(0o600)
        return FixtureStack(work)

    def compose(self, project: str, *args: str) -> list[str]:
        if project not in self.projects.values():
            raise ValueError("v12_backup_foreign_project")
        return [
            str(self.executable),
            "compose",
            "-p",
            project,
            "--env-file",
            str(self.work / "compose.env"),
            "-f",
            str(self.work / "compose.yaml"),
            *args,
        ]

    def install(self) -> None:
        store.compose = self.compose
        stack.LOCAL = self.work / "compose.env"
        combined.LOCK = self.work / "controller.lock"
        combined.MAINTENANCE = self.work / "maintenance.json"
        combined.BACKUPS = self.work / "backups"

    def url(self, project: str) -> str:
        container = store.checked_run(self.compose(project, "ps", "-q", "db")).decode().strip()
        port = docker("port", container, "5432/tcp")
        if not port.startswith("127.0.0.1:") or "\n" in port:
            raise ValueError("v12_backup_database_loopback")
        values = dict(
            line.split("=", 1) for line in (self.work / "compose.env").read_text().splitlines()
        )
        return (
            "postgresql+psycopg://ai_app:" + values["AI_DB_PASSWORD"] + "@" + port + "/retailops_ai"
        )

    def mlflow_port(self, project: str) -> int:
        container = store.checked_run(self.compose(project, "ps", "-q", "mlflow")).decode().strip()
        port = docker("port", container, "5000/tcp")
        if not port.startswith("127.0.0.1:") or "\n" in port:
            raise ValueError("v12_backup_mlflow_loopback")
        return int(port.rsplit(":", 1)[1])

    def write_artifact(self, project: str, value: bytes) -> None:
        # A deliberately unreferenced file proves backup verifies the whole volume.
        store.checked_run(
            self.compose(
                project,
                "run",
                "--rm",
                "-T",
                "--no-deps",
                "--entrypoint",
                "python",
                "mlflow",
                "-c",
                "from pathlib import Path;import base64,sys;p=Path('/var/mlflow/artifacts/backup_probe/unreferenced.txt');p.parent.mkdir(exist_ok=True);p.write_bytes(base64.b64decode(sys.argv[1]))",
                base64.b64encode(value).decode(),
            )
        )

    def cleanup(self, project: str) -> None:
        if project not in self.projects.values():
            raise ValueError("v12_backup_foreign_cleanup")
        # Verify every existing project resource before allowing Compose to remove it.
        for kind, command, field in (
            ("container", ("ps", "-aq"), "Config"),
            ("volume", ("volume", "ls", "-q"), None),
            ("network", ("network", "ls", "-q"), None),
        ):
            resources = docker(
                *command, "--filter", "label=com.docker.compose.project=" + project
            ).splitlines()
            for identity in resources:
                item = json.loads(
                    docker(*(() if kind == "container" else (kind,)), "inspect", identity)
                )[0]
                labels = item[field]["Labels"] if field else item["Labels"]
                if labels.get(OWNER_LABEL) != self.owner:
                    raise ValueError("v12_backup_cleanup_owner_mismatch")
        store.checked_run(self.compose(project, "down", "--volumes", "--remove-orphans"))
        for command in (("ps", "-aq"), ("volume", "ls", "-q"), ("network", "ls", "-q")):
            if docker(*command, "--filter", "label=com.docker.compose.project=" + project):
                raise ValueError("v12_backup_cleanup_incomplete")

    def private_invocation(self, project: str) -> Path:
        path = self.work / "invocation.json"
        raw = dict(
            owner=self.owner,
            database_url=self.url(project),
            mlflow_port=self.mlflow_port(project),
            state_file=str(self.work / "state.json"),
        )
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        return path
