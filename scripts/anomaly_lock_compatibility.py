"""Verify an explicit additive AI12 environment against the original anomaly fit lock."""

import copy
import hashlib
import tomllib
from pathlib import Path
from typing import Any

ROOT_PACKAGE = "retailops-ai-intelligence"
ADDITION = {"name": "langgraph", "specifier": "==1.2.12"}


def lock_compatibility(
    original_sha256: str, current: bytes, training_lock: Path | None
) -> dict[str, Any]:
    current_sha256 = hashlib.sha256(current).hexdigest()
    if current_sha256 == original_sha256:
        return {
            "status": "unchanged",
            "training_lock_sha256": original_sha256,
            "runtime_lock_sha256": current_sha256,
        }
    if training_lock is None:
        raise ValueError("anomaly_compatibility_dependency_lock_changed")
    with training_lock.open("rb") as stream:
        baseline = stream.read(2_000_001)
    if len(baseline) > 2_000_000 or hashlib.sha256(baseline).hexdigest() != original_sha256:
        raise ValueError("anomaly_compatibility_training_lock_binding")
    if len(current) > 2_000_000:
        raise ValueError("anomaly_compatibility_runtime_lock_budget")
    original = tomllib.loads(baseline.decode("utf-8"))
    actual = tomllib.loads(current.decode("utf-8"))
    if {k: v for k, v in original.items() if k != "package"} != {
        k: v for k, v in actual.items() if k != "package"
    }:
        raise ValueError("anomaly_compatibility_lock_environment_changed")
    before = {p["name"]: p for p in original["package"]}
    after = {p["name"]: p for p in actual["package"]}
    if len(before) != len(original["package"]) or len(after) != len(actual["package"]):
        raise ValueError("anomaly_compatibility_ambiguous_locked_package")
    if ROOT_PACKAGE not in before or not before.keys() <= after.keys():
        raise ValueError("anomaly_compatibility_original_package_missing")
    for name, package in before.items():
        if name != ROOT_PACKAGE and package != after[name]:
            raise ValueError("anomaly_compatibility_original_package_changed")
    root = copy.deepcopy(after[ROOT_PACKAGE])
    try:
        root["dependencies"].remove({"name": "langgraph"})
        root["metadata"]["requires-dist"].remove(ADDITION)
    except (KeyError, ValueError):
        raise ValueError("anomaly_compatibility_unreviewed_root_addition") from None
    if root != before[ROOT_PACKAGE] or after.get("langgraph", {}).get("version") != "1.2.12":
        raise ValueError("anomaly_compatibility_unreviewed_root_addition")
    return {
        "status": "additive_extension_verified",
        "training_lock_sha256": original_sha256,
        "runtime_lock_sha256": current_sha256,
        "original_packages_unchanged": len(before) - 1,
        "root_addition": ADDITION,
        "added_packages": sorted(after.keys() - before.keys()),
        "scope": "original_locked_packages_and_artifacts_plus_reviewed_langgraph",
    }
