"""Audited version inventory; emit the completed receipt without target values."""

import argparse
import json
import sqlite3
from pathlib import Path

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.source_replay_contract import ForecastSourceReplayProtocol
from retailops_ai.evaluation_campaign.source_versions import open_forecast_source_versions
from retailops_ai.source_snapshot.files import SnapshotError, decode_json, read_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--curated", required=True, type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--expected-protocol-sha256", required=True)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--access-plan-sha256", required=True)
    args = parser.parse_args()
    try:
        protocol = ForecastSourceReplayProtocol.model_validate_json(
            canonical_bytes(decode_json(read_bytes(args.protocol.parent, args.protocol.name)))
        )
        if canonical_sha256(protocol.model_dump(mode="json")) != args.expected_protocol_sha256:
            raise SnapshotError("forecast_source_version_cli_protocol_mismatch")
        with open_forecast_source_versions(
            args.snapshot,
            args.curated,
            protocol,
            journal=args.journal,
            plan_sha256=args.access_plan_sha256,
        ) as reader:
            pass
        print(json.dumps(reader.receipt().model_dump(mode="json"), sort_keys=True))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(
            json.dumps(
                {
                    "error_code": str(exc)
                    if isinstance(exc, SnapshotError)
                    else "forecast_source_version_rejected"
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
