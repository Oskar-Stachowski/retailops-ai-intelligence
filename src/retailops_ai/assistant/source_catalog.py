"""An immutable, minimal scope catalogue from a verified AI 03 source import."""

import hashlib
import io
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256

SourceID = Annotated[str, Field(pattern=r"^source-sha256-[0-9a-f]{64}$")]
SnapshotID = Annotated[str, Field(pattern=r"^snapshot-sha256-[0-9a-f]{64}$")]


class CatalogProduct(Contract):
    product_id: str
    available_at: UtcTime


class ChannelAssignment(Contract):
    assignment_key: str
    version: int = Field(ge=1)
    selling_location_id: str
    channel: Literal["store", "online"]
    effective_from: date
    effective_to: date
    available_at: UtcTime


class SourceCatalog(Contract):
    source_dataset_id: SourceID
    snapshot_id: SnapshotID
    manifest_sha256: Sha256
    products: tuple[CatalogProduct, ...] = Field(min_length=1, max_length=50000)
    selling_locations: tuple[str, ...] = Field(min_length=1, max_length=10000)
    assignments: tuple[ChannelAssignment, ...] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def identifiers(self) -> Self:
        product_ids = [row.product_id for row in self.products]
        if len(set(product_ids)) != len(product_ids) or len(set(self.selling_locations)) != len(
            self.selling_locations
        ):
            raise ValueError("duplicate_catalog_id")
        for value in [*product_ids, *self.selling_locations]:
            parsed = UUID(value)
            if str(parsed) != value or parsed.version not in {1, 2, 3, 4, 5}:
                raise ValueError("source_catalog_requires_canonical_uuid")
        for row in self.assignments:
            if (
                row.selling_location_id not in self.selling_locations
                or row.effective_from >= row.effective_to
            ):
                raise ValueError("invalid_channel_assignment")
        return self

    def checksum(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def load_source_catalog(path: Path) -> SourceCatalog:
    # Snapshot extras are optional for the rest of the API; loading a document runtime
    # explicitly requires the same typed import verification as AI 03.
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from retailops_ai.source_snapshot.files import read_bytes
    from retailops_ai.source_snapshot.importer import verify_import

    snapshot = verify_import(path)
    selected = {}
    for name, limit in (
        ("product_catalog", 50000),
        ("selling_locations", 10000),
        ("channel_assignments", 10000),
    ):
        table = next(row for row in snapshot.manifest["tables"] if row["table"] == name)
        if table["row_count"] > limit:
            raise ValueError("source_catalog_row_limit")
        rows = []
        for reference in table["files"]:
            raw = read_bytes(path / "snapshot", reference["path"], 32 * 1024 * 1024)
            if (
                len(raw) != reference["bytes"]
                or hashlib.sha256(raw).hexdigest() != reference["sha256"]
            ):
                raise ValueError("source_changed_after_verification")
            with pq.ParquetFile(io.BytesIO(raw), arrow_extensions_enabled=False) as parquet:
                for batch in parquet.iter_batches(batch_size=8192, use_threads=False):
                    rows.extend(batch.to_pylist())
                    if len(rows) > limit:
                        raise ValueError("source_catalog_row_limit")
        selected[name] = rows
    return SourceCatalog.model_validate_json(
        json.dumps(
            {
                "source_dataset_id": snapshot.source_id,
                "snapshot_id": snapshot.snapshot_id,
                "manifest_sha256": snapshot.manifest_sha256,
                "products": sorted(
                    [
                        {"product_id": row["id"], "available_at": row["available_at"].isoformat()}
                        for row in selected["product_catalog"]
                    ],
                    key=lambda row: row["product_id"],
                ),
                "selling_locations": sorted(row["id"] for row in selected["selling_locations"]),
                "assignments": [
                    {
                        key: row[key].isoformat() if hasattr(row[key], "isoformat") else row[key]
                        for key in ChannelAssignment.model_fields
                    }
                    for row in sorted(
                        selected["channel_assignments"],
                        key=lambda row: (
                            row["assignment_key"],
                            row["version"],
                            row["available_at"],
                        ),
                    )
                ],
            }
        )
    )
