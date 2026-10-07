"""Read one scoped source page to a new private output file."""

from __future__ import annotations

import argparse
import json
import os
import stat
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError

from . import wire
from .client import ClientConfig, SourceClient, SourceReadError

MODELS: dict[str, tuple[type[wire.QueryModel], type[wire.ReadModel]]] = {
    "products": (wire.ProductsQuery, wire.ProductListResponse),
    "sales": (wire.SalesQuery, wire.SaleListResponse),
    "inventory-snapshots": (wire.InventoryQuery, wire.InventorySnapshotListResponse),
    "forecasts": (wire.ForecastsQuery, wire.ForecastListResponse),
    "inventory-risks": (wire.RisksQuery, wire.StockRiskListResponse),
}


def private_bytes(path: Path) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise SourceReadError("private_file_required")
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise SourceReadError("private_file_too_large")
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--query", type=Path, required=True)
    parser.add_argument("--resource", choices=sorted(MODELS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = ClientConfig.model_validate_json(private_bytes(args.config))
        query_type, response_type = MODELS[args.resource]
        query = query_type.model_validate_json(private_bytes(args.query))
        result = SourceClient(config)._get(args.resource, query, response_type, None)
        content = (
            json.dumps(
                {
                    "value": result.value.model_dump(mode="json"),
                    "metadata": asdict(result.metadata),
                },
                default=str,
                allow_nan=False,
                sort_keys=True,
            ).encode()
            + b"\n"
        )
        descriptor = os.open(
            args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        print(
            json.dumps(
                {"status": "passed", "read_mode": "bounded_live", "snapshot_supported": False}
            )
        )
        return 0
    except SourceReadError as error:
        print(json.dumps({"code": error.code, "retryable": error.retryable}))
        return 2
    except (OSError, ValidationError, ValueError):
        print(json.dumps({"code": "invalid_private_input_or_output", "retryable": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
