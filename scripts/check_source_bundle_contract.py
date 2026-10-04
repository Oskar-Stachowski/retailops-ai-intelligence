"""Verify immutable wire/native-owner copies and the isolated transfer lock."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    pin = json.loads((ROOT / "src/retailops_ai/source_bundle/upstream.json").read_bytes())
    for name in (
        "source_contract_commit",
        "source_native_producer_commit",
        "ai_native_importer_commit",
    ):
        if not re.fullmatch(r"[0-9a-f]{40}", pin[name]):
            raise ValueError("immutable_owner_required")
    for relative, expected in pin["files"].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("source_bundle_owner_copy_changed: " + relative)
    from retailops_ai.data_contracts.identity import canonical_sha256
    from retailops_ai.forecasting.functional_v12_campaign import campaign_code

    if canonical_sha256(campaign_code()) != pin["frozen_v12_campaign_pin_sha256"]:
        raise ValueError("frozen_v12_campaign_code_changed")
    print("Source bundle wire, native importer/assets and isolated lock pins passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
