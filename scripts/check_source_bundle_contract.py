"""Verify immutable wire/native-owner copies and the isolated transfer lock."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def integration_bindings() -> tuple[Path, str] | None:
    """Bind this assistant integration without rewriting its immutable AI10 owner pin."""
    path = ROOT / "agent/source-bundle.prepaid.v2.json"
    if not path.exists():
        return None
    integration = json.loads(path.read_bytes())
    owner = ROOT / "src/retailops_ai/source_bundle/upstream.json"
    frozen = ROOT / "environments/anomaly/qualification.uv.lock"
    if (
        integration["version"] != "ai12-source-bundle-integration-1.0"
        or hashlib.sha256(owner.read_bytes()).hexdigest() != integration["owner_pin_sha256"]
        or hashlib.sha256(frozen.read_bytes()).hexdigest()
        != integration["owner_dependency_lock_sha256"]
        or hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
        != integration["integration_dependency_lock_sha256"]
    ):
        raise ValueError("source_bundle_assistant_integration_binding_changed")
    return frozen, str(integration["integration_campaign_pin_sha256"])


def main() -> int:
    pin = json.loads((ROOT / "src/retailops_ai/source_bundle/upstream.json").read_bytes())
    integration = integration_bindings()
    for name in (
        "source_contract_commit",
        "source_native_producer_commit",
        "ai_native_importer_commit",
        "ai_model_snapshot_importer_commit",
    ):
        if not re.fullmatch(r"[0-9a-f]{40}", pin[name]):
            raise ValueError("immutable_owner_required")
    for relative, expected in pin["files"].items():
        path = (
            integration[0] if integration is not None and relative == "uv.lock" else ROOT / relative
        )
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("source_bundle_owner_copy_changed: " + relative)
    from retailops_ai.data_contracts.identity import canonical_sha256
    from retailops_ai.forecasting.functional_v12_campaign import campaign_code

    shared = pin.get(
        "shared_campaign_implementation_pin_sha256", pin["accepted_main_campaign_pin_sha256"]
    )
    if "shared_campaign_implementation_commit" in pin and not re.fullmatch(
        r"[0-9a-f]{40}", pin["shared_campaign_implementation_commit"]
    ):
        raise ValueError("immutable_shared_campaign_owner_required")
    expected_campaign = integration[1] if integration is not None else shared
    if canonical_sha256(campaign_code()) != expected_campaign:
        raise ValueError("accepted_main_campaign_code_changed")
    print("Source bundle wire, native importer/assets and isolated lock pins passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
