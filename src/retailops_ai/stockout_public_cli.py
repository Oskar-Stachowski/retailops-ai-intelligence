"""Prepare or fully verify public physical inference inputs; no truth flag exists."""

import argparse
import json
import sqlite3
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from retailops_ai.data_contracts.common import UtcTime
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout.artifacts import write_artifact
from retailops_ai.stockout_public_inputs import prepare_inputs
from retailops_ai.stockout_runtime.inputs import MAX_INPUT_BYTES, PhysicalScope
from retailops_ai.stockout_training.cli import read_bounded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inputs-build", "inputs-verify"))
    for name in ("curated", "features", "upstream", "scope", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    try:
        scope = PhysicalScope.model_validate(read_bounded(args.scope, 1024**2))
        result = prepare_inputs(
            args.curated,
            args.features,
            args.upstream,
            scope=scope,
            as_of=TypeAdapter(UtcTime).validate_python(args.as_of),
        )
        document = result.model_dump(mode="json")
        if args.action == "inputs-verify":
            if read_bounded(args.output, MAX_INPUT_BYTES) != document:
                raise ValueError("stockout_runtime_inputs_full_replay_mismatch")
            status = "verified"
        else:
            status = write_artifact(document, args.output, max_bytes=MAX_INPUT_BYTES)
        print(
            json.dumps(
                dict(
                    status=status,
                    inputs_id=result.inputs_id,
                    rows=len(result.points),
                    source_freshness_status="unknown",
                    model_promoted=False,
                )
            )
        )
        return 0
    except (
        SnapshotError,
        ValidationError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
    ):
        print(json.dumps(dict(status="failed", reason="invalid_stockout_public_input_or_artifact")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
