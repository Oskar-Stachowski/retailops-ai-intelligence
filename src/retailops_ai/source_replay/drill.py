"""Offline acceptance over an actual immutable native fixture and frozen curation."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from retailops_ai.curated.builder import build_curated, iter_rows
from retailops_ai.curated.reader import CuratedReader
from retailops_ai.source_snapshot.importer import import_snapshot

from .history import ObservationHistory, ReplayError
from .wire import Envelope, ObservationVersion, Record, Stream, canonical


def run_drill(snapshot: Path, workspace: Path) -> dict[str, Any]:
    imported = import_snapshot(snapshot, workspace / "data/generated")
    curated = build_curated(imported.directory, workspace / "data/generated")
    reader = CuratedReader(curated.directory)
    spec = next(
        table
        for table in imported.snapshot.manifest["tables"]
        if table["table"] == "daily_demand_versions"
    )
    rows = sorted(
        (
            ObservationVersion.model_validate(row)
            for row in iter_rows(imported.directory / "snapshot", spec["files"], 1024)
        ),
        key=lambda row: (row.version, row.key),
    )
    stream = Stream(
        source_authority_id=str(uuid5(NAMESPACE_URL, imported.snapshot.source_id)),
        cluster_id="offline-fixture-only",
        topic_id="offline-fixture-topic+/",
    )
    offsets = [0, 0, 0]
    records = []
    for row in rows:
        partition = int(row.observation_id.replace("-", ""), 16) % 3
        records.append(
            Record(
                partition=partition,
                offset=offsets[partition],
                envelope=Envelope(
                    source_authority_id=stream.source_authority_id,
                    event_id=str(uuid5(NAMESPACE_URL, "fixture-event-" + row.id)),
                    fact=row,
                ),
            )
        )
        offsets[partition] += 1
    genesis = ObservationHistory(stream, partitions=3)
    full = genesis.apply_batch(records, stream=stream)
    baseline = [record for record in records if record.envelope.fact.version == 1]
    prefix = baseline[: len(baseline) // 2]
    captured = genesis.apply_batch(prefix, stream=stream).capture()
    restored = ObservationHistory.restore(canonical(captured), stream=stream)
    replayed = restored.apply_batch(records, stream=stream)
    if canonical(full.capture()) != canonical(replayed.capture()):
        raise ReplayError("drill_snapshot_overlap_differs_from_full_replay")
    origins = sorted(
        {
            min(row.available_at for row in rows) - timedelta(microseconds=1),
            min(row.available_at for row in rows) + timedelta(days=14),
            max(row.available_at for row in rows) + timedelta(days=1),
        }
    )
    totals = []
    for origin in origins:
        expected = tuple(
            sorted(
                (
                    ObservationVersion.model_validate(
                        {key: row[key] for key in ObservationVersion.model_fields}
                    )
                    for row in reader.rows(origin)
                ),
                key=lambda row: row.grain,
            )
        )
        if full.as_of(origin) != expected:
            raise ReplayError("drill_native_replay_differs_from_frozen_curated_reader")
        totals.append(
            {
                "origin": origin.isoformat(),
                "observations": len(expected),
                "units": full.quantity_total(origin),
            }
        )
    origin = origins[-1] + timedelta(days=1)
    original = next(
        row for row in full.as_of(origin) if row.observation_status == "observed_positive"
    )
    correction = ObservationVersion.model_validate(
        {
            **original.model_dump(),
            "id": str(uuid5(NAMESPACE_URL, "fixture-correction-" + original.id)),
            "version": original.version + 1,
            "observed_units": original.observed_units + 3
            if original.observed_units is not None
            else 3,
            "available_at": origin,
        }
    )
    partition = int(original.observation_id.replace("-", ""), 16) % 3
    corrected = Record(
        partition=partition,
        offset=offsets[partition],
        envelope=Envelope(
            source_authority_id=stream.source_authority_id,
            event_id=str(uuid5(NAMESPACE_URL, "fixture-correction-event")),
            fact=correction,
        ),
    )
    duplicated = Record(
        partition=partition,
        offset=offsets[partition] + 1,
        envelope=Envelope(
            source_authority_id=stream.source_authority_id,
            event_id=str(uuid5(NAMESPACE_URL, "fixture-correction-new-envelope")),
            fact=correction,
        ),
    )
    direct = full.apply_batch([corrected, duplicated], stream=stream)
    overlap = restored.apply_batch([*records, corrected, duplicated], stream=stream)
    before, after = full.quantity_total(origin), direct.quantity_total(origin)
    if (
        canonical(direct.capture()) != canonical(overlap.capture())
        or len(direct.rows) != len(full.rows) + 1
        or after != before + 3
        or direct.quantity_total(origin - timedelta(microseconds=1)) != before
    ):
        raise ReplayError("drill_business_correction_or_dedup_failed")
    return {
        "status": "passed",
        "scope": "offline daily_demand_versions receiver mechanics; transport and correction are explicit fixtures",
        "source_snapshot_id": imported.snapshot.snapshot_id,
        "source_dataset_id": imported.snapshot.source_id,
        "source_snapshot_version": imported.snapshot.manifest["schema_version"],
        "native_versions": len(rows),
        "captured_native_versions": len(captured.rows),
        "partitions": 3,
        "captured_next_offsets": [boundary.model_dump() for boundary in captured.boundaries],
        "final_next_offsets": [boundary.model_dump() for boundary in direct.boundaries],
        "capture_id": captured.capture_id,
        "final_capture_id": direct.capture().capture_id,
        "final_capture_sha256": hashlib.sha256(canonical(direct.capture())).hexdigest(),
        "as_of": totals,
        "units_before_correction": before,
        "units_after_correction": after,
        "overlap_full_replay_equal": True,
        "business_dedup_and_correction_equal": True,
        "curated_as_of_equal": True,
        "source_live_capture_supported": False,
        "broker_ack_performed": False,
        "full_43_table_handoff": False,
    }
