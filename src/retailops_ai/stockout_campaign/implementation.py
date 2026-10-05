"""Pin final orchestration and the existing portable prediction/measurement code."""

import hashlib
from importlib.resources import files

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.source_snapshot.protocol import resource_bytes


def code_digest() -> str:
    refs = {}
    for package in (
        "stockout_campaign",
        "stockout_selection",
        "stockout_training",
        "stockout_policy",
        "stockout_runtime",
        "stockout_qualification",
        "stockout_temporal_series",
        "stockout_temporal_storage",
    ):
        for ref in files("retailops_ai." + package).iterdir():
            if ref.is_file() and ref.name.endswith(".py"):
                refs[package + "/" + ref.name] = hashlib.sha256(ref.read_bytes()).hexdigest()
    return canonical_sha256(refs)


def lock_digest() -> str:
    return hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest()
