"""Typed upstream points and selling-series lineage, with nullable seven-day forecasts."""

from typing import Any

from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout.upstream_contract import UpstreamPoint


def schema() -> Any:
    import pyarrow as pa  # type: ignore[import-untyped]

    timestamp = pa.timestamp("us", tz="UTC")
    series = pa.struct(
        [
            pa.field("selling_location_id", pa.string(), nullable=False),
            pa.field("channel", pa.string(), nullable=False),
            pa.field("route_record_sha256", pa.string(), nullable=False),
            pa.field("history_context_sha256", pa.string(), nullable=False),
            pa.field("input_rows_sha256", pa.string(), nullable=False),
            pa.field("source_available_at", timestamp),
            pa.field("daily_units", pa.list_(pa.float64()), nullable=False),
            pa.field("reason", pa.string()),
        ]
    )
    return pa.schema(
        [
            pa.field("product_id", pa.string(), nullable=False),
            pa.field("stock_location_id", pa.string(), nullable=False),
            pa.field("as_of", timestamp, nullable=False),
            pa.field("forecast_origin", timestamp, nullable=False),
            pa.field("training_cutoff", timestamp, nullable=False),
            pa.field("selection_cutoff", timestamp, nullable=False),
            pa.field("source_available_at", timestamp),
            pa.field("upstream_model_version", pa.string(), nullable=False),
            pa.field("status", pa.string(), nullable=False),
            pa.field("reason", pa.string()),
            pa.field("forecast_units_7d", pa.float64()),
            pa.field("series", pa.list_(series), nullable=False),
        ]
    )


def encode(points: list[UpstreamPoint], max_bytes: int) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    table = pa.Table.from_pylist([p.model_dump(mode="python") for p in points], schema=schema())
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", write_statistics=True)
    raw = bytes(sink.getvalue().to_pybytes())
    if len(raw) > max_bytes:
        raise SnapshotError("stockout_upstream_partition_byte_limit")
    return raw
