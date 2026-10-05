"""Explicit Arrow schemas; nullable numbers never rely on batch inference."""

from typing import Any

from retailops_ai.source_snapshot.files import SnapshotError, json_sha256
from retailops_ai.stockout.feature_contract import FeaturePoint

INTEGER_VALUES = {
    "available_qty",
    "history_known_days",
    "history_missing_days",
    "history_in_stock_days",
    "history_constrained_days",
    "history_inventory_unknown_days",
    "historical_stockout_onsets",
    "open_order_quantity",
    "due_within_7d_quantity",
    "overdue_order_quantity",
}


def schemas() -> dict[str, Any]:
    import pyarrow as pa  # type: ignore[import-untyped]

    from retailops_ai.stockout.feature_contract import FeatureValues

    timestamp = pa.timestamp("us", tz="UTC")
    values = pa.struct(
        [
            pa.field(name, pa.int64() if name in INTEGER_VALUES else pa.float64())
            for name in FeatureValues.model_fields
        ]
    )
    return {
        "points": pa.schema(
            [
                ("product_id", pa.string()),
                ("stock_location_id", pa.string()),
                ("as_of", timestamp),
                ("status", pa.string()),
                ("reason", pa.string()),
                ("feature_available_at", timestamp),
                ("values", values),
                ("history_refs", pa.list_(pa.string())),
                ("lineage_refs", pa.list_(pa.string())),
            ]
        ),
        "history": pa.schema(
            [
                ("id", pa.string()),
                ("business_date", pa.date32()),
                ("observed_units", pa.int64()),
                ("inventory_day_verified", pa.bool_()),
                ("in_stock_all_day", pa.bool_()),
                ("stockout_onsets", pa.int64()),
            ]
        ),
        "lineage": pa.schema(
            [
                ("id", pa.string()),
                ("table", pa.string()),
                ("rows", pa.int64()),
                ("source_records_sha256", pa.string()),
                ("max_available_at", timestamp),
            ]
        ),
    }


def encode_partition(points: list[FeaturePoint], max_bytes: int) -> dict[str, bytes]:
    import pyarrow as pa
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    data: dict[str, list[dict[str, Any]]] = {"points": [], "history": [], "lineage": []}
    dictionaries: dict[str, dict[str, dict[str, Any]]] = {"history": {}, "lineage": {}}
    for point in points:
        body = point.model_dump(mode="python")
        wire = point.model_dump(mode="json")
        for role in ("history", "lineage"):
            references = []
            for row, encoded in zip(body.pop(role), wire[role], strict=True):
                key = json_sha256(encoded)
                dictionaries[role][key] = {"id": key, **row}
                references.append(key)
            body[role + "_refs"] = references
        data["points"].append(body)
    for role, dictionary in dictionaries.items():
        data[role] = [dictionary[key] for key in sorted(dictionary)]
    output = {}
    for role, schema in schemas().items():
        table = pa.Table.from_pylist(data[role], schema=schema)
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink, compression="zstd", version="2.6", write_statistics=True)
        output[role] = sink.getvalue().to_pybytes()
    if sum(len(raw) for raw in output.values()) > max_bytes:
        raise SnapshotError("stockout_partition_byte_limit")
    return output
