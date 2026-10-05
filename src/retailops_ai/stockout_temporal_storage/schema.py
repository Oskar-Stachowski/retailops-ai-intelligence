"""Comparison features and membership metadata; no outcome column is serialized."""

from datetime import datetime
from typing import Any

from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout.feature_contract import FeatureValues
from retailops_ai.stockout_preparation.schema import INTEGER_VALUES


def schemas() -> dict[str, Any]:
    import pyarrow as pa  # type: ignore[import-untyped]

    timestamp = pa.timestamp("us", tz="UTC")
    key = [
        pa.field("product_id", pa.string(), nullable=False),
        pa.field("stock_location_id", pa.string(), nullable=False),
        pa.field("as_of", timestamp, nullable=False),
    ]
    return dict(
        comparison=pa.schema(
            [
                *key,
                ("status", pa.string()),
                ("reason", pa.string()),
                (
                    "base",
                    pa.struct(
                        [
                            (name, pa.int64() if name in INTEGER_VALUES else pa.float64())
                            for name in FeatureValues.model_fields
                        ]
                    ),
                ),
                (
                    "upstream",
                    pa.struct(
                        [
                            ("forecast_units_7d", pa.float64()),
                            ("forecast_days_of_supply", pa.float64()),
                            ("forecast_unavailable", pa.int64()),
                        ]
                    ),
                ),
            ]
        ),
        membership=pa.schema(
            [
                *key,
                ("role", pa.string()),
                ("eligible", pa.bool_()),
                ("reason", pa.string()),
                ("label_available_at", timestamp),
                ("window_end_at", timestamp),
            ]
        ),
    )


def encode(rows: dict[str, list[dict[str, Any]]], max_bytes: int) -> dict[str, bytes]:
    import pyarrow as pa
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    output = {}
    for role, schema in schemas().items():
        data = [
            {
                k: datetime.fromisoformat(v)
                if k in {"as_of", "label_available_at", "window_end_at"} and v is not None
                else v
                for k, v in row.items()
            }
            for row in rows[role]
        ]
        sink = pa.BufferOutputStream()
        pq.write_table(
            pa.Table.from_pylist(data, schema=schema),
            sink,
            compression="zstd",
            version="2.6",
            write_statistics=True,
        )
        output[role] = sink.getvalue().to_pybytes()
    if sum(map(len, output.values())) > max_bytes:
        raise SnapshotError("stockout_temporal_partition_resource_limit")
    return output
