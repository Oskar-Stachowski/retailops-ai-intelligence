"""Verify one diagnostic role under a frozen plan; emit counts, never target rows."""

import argparse
import json
import sqlite3
from pathlib import Path

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.label_contract import ForecastOutcomeReadProtocol
from retailops_ai.evaluation_campaign.labels import open_forecast_outcomes
from retailops_ai.evaluation_campaign.outcome_contract import OutcomeAccessBinding
from retailops_ai.source_snapshot.files import SnapshotError, decode_json, read_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", required=True, type=Path)
    parser.add_argument("--partitions", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--expected-protocol-sha256", required=True)
    parser.add_argument("--binding", required=True, type=Path)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--access-plan-sha256", required=True)
    args = parser.parse_args()
    try:
        protocol = ForecastOutcomeReadProtocol.model_validate_json(
            canonical_bytes(decode_json(read_bytes(args.protocol.parent, args.protocol.name)))
        )
        if canonical_sha256(protocol.model_dump(mode="json")) != args.expected_protocol_sha256:
            raise SnapshotError("forecast_outcome_cli_protocol_mismatch")
        binding = OutcomeAccessBinding.model_validate_json(
            canonical_bytes(decode_json(read_bytes(args.binding.parent, args.binding.name, 8192)))
        )
        with open_forecast_outcomes(
            args.features,
            args.partitions,
            args.evidence,
            protocol,
            journal=args.journal,
            plan_sha256=args.access_plan_sha256,
            binding=binding,
        ) as reader:
            result = reader.summary()
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(
            json.dumps(
                {
                    "error_code": str(exc)
                    if isinstance(exc, SnapshotError)
                    else "forecast_outcome_reader_rejected"
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
