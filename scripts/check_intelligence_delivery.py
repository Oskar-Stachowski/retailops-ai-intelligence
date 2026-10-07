"""Guard the frozen ML lock and isolate transport dependencies from model identity."""

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

CORE_LOCK_SHA256 = "33c53d1a1f08d5c90b3b61c79e6aeb732f0eebc6e8be36e735c93ca277492587"


def registry_versions(document: dict[str, Any]) -> dict[str, str]:
    return {
        package["name"]: package["version"]
        for package in document["package"]
        if "registry" in package["source"]
    }


def check(core: Path, delivery: Path, project: Path) -> dict[str, Any]:
    raw = core.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CORE_LOCK_SHA256:
        raise ValueError("intelligence_delivery_frozen_ml_lock_changed")
    original = registry_versions(tomllib.loads(raw.decode()))
    isolated = registry_versions(tomllib.loads(delivery.read_text()))
    if "confluent-kafka" in original or isolated.get("confluent-kafka") != "2.15.1":
        raise ValueError("intelligence_delivery_client_not_isolated_or_pinned")
    if set(isolated) - set(original) != {"confluent-kafka"} or any(
        isolated[name] != original[name] for name in isolated.keys() & original.keys()
    ):
        raise ValueError("intelligence_delivery_ml_dependency_drift")
    configuration = tomllib.loads(project.read_text())
    expected = sorted(f"{name}=={version}" for name, version in original.items())
    if sorted(configuration["tool"]["uv"]["constraint-dependencies"]) != expected or configuration[
        "tool"
    ]["uv"]["sources"]["retailops-ai-intelligence"] != {"path": "../..", "editable": True}:
        raise ValueError("intelligence_delivery_core_constraints_or_source_changed")
    return {
        "status": "passed",
        "core_lock_sha256": CORE_LOCK_SHA256,
        "delivery_registry_packages": len(isolated),
        "transport_package": "confluent-kafka==2.15.1",
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    worker = root / "tools/intelligence-delivery"
    print(json.dumps(check(root / "uv.lock", worker / "uv.lock", worker / "pyproject.toml")))
