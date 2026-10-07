"""Retain the exact committed native outbox census before an owned acceptance DB is removed."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.intelligence_events.model_contracts import (
    AnomalyDetected,
    ModelEvent,
    StockoutRiskScored,
)
from retailops_ai.source_snapshot.files import checked_directory, read_bytes
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

MAX_ROWS = 1400
MAX_BYTES = 64 * 1024**2


def event_document(document: object) -> ModelEvent:
    raw = canonical_bytes(document)
    if not isinstance(document, dict):
        raise ValueError("native_outbox_export_document")
    kind = document.get("event_type")
    if kind == "anomaly_detected":
        return AnomalyDetected.model_validate_json(raw)
    if kind == "stockout_risk_scored":
        return StockoutRiskScored.model_validate_json(raw)
    raise ValueError("native_outbox_export_event_type")


def seal_census(
    expected: Sequence[ModelEvent], rows: Sequence[Mapping[str, Any]], *, environment: str
) -> dict[str, bytes]:
    """Require every original native result; this receipt does not attest model quality or delivery."""
    if environment not in {"local", "test"} or not 1 <= len(expected) <= MAX_ROWS:
        raise ValueError("native_outbox_export_environment_or_size")
    verified = [event_document(event.model_dump(mode="json")) for event in expected]
    by_id = {str(event.event_id): event for event in verified}
    if len(by_id) != len(verified) or len(rows) != len(verified):
        raise ValueError("native_outbox_export_incomplete_census")
    documents: dict[str, bytes] = {}
    members: list[dict[str, Any]] = []
    for row in rows:
        event = event_document(row["document"])
        identity = str(event.event_id)
        result = (
            event.payload.anomaly_id
            if isinstance(event, AnomalyDetected)
            else event.payload.risk_id
        )
        if (
            event != by_id.get(identity)
            or identity in documents
            or str(row["event_id"]) != identity
            or row["event_type"] != event.event_type
            or row["result_id"] != result
            or row["environment"] != environment
            or row["topic"] != event.topic
            or row["partition_key"] != event.partition_key
        ):
            raise ValueError("native_outbox_export_stored_binding")
        delivered, partition, offset = (
            row["delivered_at"],
            row["delivered_partition"],
            row["delivered_offset"],
        )
        if delivered is None:
            if partition is not None or offset is not None:
                raise ValueError("native_outbox_export_partial_delivery_receipt")
        elif (
            not isinstance(delivered, datetime)
            or delivered.tzinfo is None
            or type(partition) is not int
            or partition < 0
            or type(offset) is not int
            or offset < 0
        ):
            raise ValueError("native_outbox_export_partial_delivery_receipt")
        raw = canonical_bytes(event.model_dump(mode="json"))
        documents[identity] = raw
        members.append(
            dict(
                event_id=identity,
                event_type=event.event_type,
                result_id=result,
                correlation_id=str(event.correlation_id),
                partition_key=event.partition_key,
                event_sha256=hashlib.sha256(raw).hexdigest(),
                payload_sha256=canonical_sha256(event.payload.model_dump(mode="json")),
                delivered_at=None if delivered is None else delivered.isoformat(),
                delivered_partition=partition,
                delivered_offset=offset,
            )
        )
    raw = b"".join(documents[identity] + b"\n" for identity in sorted(documents))
    if len(raw) > MAX_BYTES:
        raise ValueError("native_outbox_export_byte_limit")
    content = dict(
        version="native-model-outbox-census-1.0",
        environment=environment,
        topic="retailops.intelligence.v2",
        evidence_class="committed_native_publication_census",
        model_quality_attestation=False,
        transport_status="delivered"
        if all(r["delivered_at"] is not None for r in rows)
        else "retained",
        events=dict(
            path="events.jsonl",
            rows=len(documents),
            size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        ),
        members=sorted(members, key=lambda member: member["event_id"]),
    )
    content["census_id"] = "native-model-outbox-sha256-" + canonical_sha256(content)
    return {"events.jsonl": raw, "receipt.json": canonical_bytes(content) + b"\n"}


def export_committed_model_events(
    engine: Engine, expected: Sequence[ModelEvent], output: Path, *, environment: str
) -> Path:
    if not expected or len(expected) > MAX_ROWS:
        raise ValueError("native_outbox_export_size")
    batches = sorted({(event.event_type, str(event.correlation_id)) for event in expected})
    rows: list[Mapping[str, Any]] = []
    with engine.begin() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout='3s'"))
        for kind, batch in batches:
            rows.extend(
                dict(row)
                for row in connection.execute(
                    text("""
SELECT event_id,event_type,result_id,environment,topic,partition_key,document,
 delivered_at,delivered_partition,delivered_offset
FROM ai.model_intelligence_outbox
WHERE environment=:environment AND event_type=:kind AND document->>'correlation_id'=:batch
ORDER BY event_id LIMIT 1401
"""),
                    dict(environment=environment, kind=kind, batch=batch),
                )
                .mappings()
                .all()
            )
            if len(rows) > MAX_ROWS:
                raise ValueError("native_outbox_export_size")
    files = seal_census(expected, rows, environment=environment)
    identity: str = json.loads(files["receipt.json"])["census_id"]
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked_directory(output)
    with tempfile.TemporaryDirectory(prefix=".native-outbox-", dir=output) as directory:
        staging = Path(directory)
        staging.chmod(0o700)
        for name, raw in files.items():
            path = staging / name
            path.write_bytes(raw)
            path.chmod(0o600)
        fsync_tree(staging)
        destination = output / identity
        try:
            publish_noreplace(staging, destination)
        except FileExistsError:
            if any(read_bytes(destination, name, MAX_BYTES) != raw for name, raw in files.items()):
                raise ValueError("native_outbox_export_publication_conflict") from None
    return destination
