"""Explicit private label schema, preserving nullable outcomes and UTC boundaries."""

from typing import Any

from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout.contract import LabelPoint


def schema() -> Any:
    import pyarrow as pa  # type: ignore[import-untyped]

    timestamp = pa.timestamp("us", tz="UTC")
    return pa.schema(
        [
            pa.field("product_id", pa.string(), nullable=False),
            pa.field("stock_location_id", pa.string(), nullable=False),
            pa.field("as_of", timestamp, nullable=False),
            pa.field("window_end_at", timestamp, nullable=False),
            pa.field("evaluated_at", timestamp, nullable=False),
            pa.field("status", pa.string(), nullable=False),
            pa.field("reason", pa.string()),
            pa.field("incident_stockout", pa.int64()),
            pa.field("label_available_at", timestamp),
            pa.field("first_incident_at", timestamp),
            pa.field("first_incident_event_id", pa.string()),
        ]
    )


def encode(points: list[LabelPoint], max_bytes: int) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    table = pa.Table.from_pylist([p.model_dump(mode="python") for p in points], schema=schema())
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", write_statistics=True)
    raw = bytes(sink.getvalue().to_pybytes())
    if len(raw) > max_bytes:
        raise SnapshotError("stockout_label_partition_byte_limit")
    return raw
