"""Read exactly one authorized role; join all its covariates with bounded disk indexes."""

import hashlib
import sqlite3
import zlib
from collections.abc import Iterator
from itertools import islice
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_forecast_inputs import _horizons
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScorePlan
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.evaluation_campaign.physical_contract import (
    PhysicalForecastExample,
    PhysicalForecastManifest,
)
from retailops_ai.evaluation_campaign.physical_versions import check_index
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.manifests import input_models
from retailops_ai.source_snapshot.files import SnapshotError, regular_file

Record = tuple[bytes, InputRow, PhysicalForecastExample]
Window = tuple[list[Record], HistoryContext]


def index_role(
    db: sqlite3.Connection,
    dataset: Path,
    manifest: PhysicalForecastManifest,
    plan: CampaignForecastScorePlan,
) -> dict[str, Any]:
    plan = CampaignForecastScorePlan.model_validate_json(
        canonical_bytes(plan.model_dump(mode="json"))
    )
    db.execute(
        "CREATE TABLE examples(key BLOB PRIMARY KEY,eligible INTEGER,body BLOB,feature BLOB,history TEXT,group_key BLOB,horizon INTEGER)"
    )
    db.execute("CREATE INDEX examples_group ON examples(group_key,horizon)")
    db.execute("CREATE INDEX examples_history ON examples(history)")
    content, keys, eligible_keys = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    count = size = eligible = 0
    previous = None
    with regular_file(dataset, plan.role + ".jsonl") as stream:
        while line := stream.readline(manifest.descriptor.recipe.max_record_bytes + 1):
            if len(line) > manifest.descriptor.recipe.max_record_bytes or not line.endswith(b"\n"):
                raise SnapshotError("campaign_score_role_record_limit")
            example = PhysicalForecastExample.model_validate_json(line)
            key = membership_key(example.membership)
            if (
                example.membership.role != plan.role
                or example.outcome is None
                or (previous is not None and key <= previous)
                or line != canonical_bytes(example.model_dump(mode="json")) + b"\n"
            ):
                raise SnapshotError("campaign_score_noncanonical_or_wrong_role")
            previous = key
            count += 1
            if count > plan.max_rows:
                raise SnapshotError("campaign_score_complete_population_limit")
            size += len(line)
            content.update(line)
            keys.update(key + b"\n")
            if example.outcome.eligible:
                eligible += 1
                eligible_keys.update(key + b"\n")
            db.execute(
                "INSERT INTO examples(key,eligible,body) VALUES(?,?,?)",
                (key, int(example.outcome.eligible), zlib.compress(line, 1)),
            )
            if count % 256 == 0:
                check_index(db, plan.max_index_bytes)
    expected = manifest.descriptor.populations[plan.role]
    if (count, size, content.hexdigest(), keys.hexdigest(), eligible) != (
        expected.row_count,
        expected.size_bytes,
        expected.sha256,
        expected.keys_sha256,
        expected.eligible_rows,
    ) or count == 0:
        raise SnapshotError("campaign_score_complete_role_receipt_mismatch")
    for index, row in enumerate(input_models(dataset / "features", "features"), 1):
        if not isinstance(row, InputRow):
            raise SnapshotError("campaign_score_feature_schema")
        key = membership_key(row)
        found = db.execute("SELECT body,feature FROM examples WHERE key=?", (key,)).fetchone()
        if found is not None:
            example = PhysicalForecastExample.model_validate_json(zlib.decompress(found[0]))
            if (
                found[1] is not None
                or example.membership.feature_row_sha256
                != hashlib.sha256(canonical_bytes(row.model_dump(mode="json"))).hexdigest()
            ):
                raise SnapshotError("campaign_score_feature_role_hash_mismatch")
            group = canonical_bytes(
                [
                    row.forecast_origin.isoformat(),
                    row.product_id,
                    row.selling_location_id,
                    row.channel,
                ]
            )
            db.execute(
                "UPDATE examples SET feature=?,history=?,group_key=?,horizon=? WHERE key=?",
                (
                    zlib.compress(canonical_bytes(row.model_dump(mode="json")), 1),
                    row.history_context_sha256,
                    group,
                    row.horizon_days,
                    key,
                ),
            )
        if index % 256 == 0:
            check_index(db, plan.max_index_bytes)
    if db.execute("SELECT 1 FROM examples WHERE feature IS NULL LIMIT 1").fetchone():
        raise SnapshotError("campaign_score_missing_feature_key")
    db.execute("CREATE TABLE histories(hash TEXT PRIMARY KEY,body BLOB)")
    for index, history in enumerate(input_models(dataset / "features", "history"), 1):
        if not isinstance(history, HistoryContext):
            raise SnapshotError("campaign_score_history_schema")
        digest = history.content_sha256()
        if db.execute("SELECT 1 FROM examples WHERE history=? LIMIT 1", (digest,)).fetchone():
            db.execute(
                "INSERT INTO histories VALUES(?,?)",
                (digest, zlib.compress(canonical_bytes(history.model_dump(mode="json")), 1)),
            )
        if index % 256 == 0:
            check_index(db, plan.max_index_bytes)
    if db.execute(
        "SELECT 1 FROM examples e LEFT JOIN histories h ON e.history=h.hash WHERE h.hash IS NULL LIMIT 1"
    ).fetchone():
        raise SnapshotError("campaign_score_missing_history")
    check_index(db, plan.max_index_bytes)
    return {
        "rows": count,
        "eligible_rows": eligible,
        "keys_sha256": keys.hexdigest(),
        "eligible_keys_sha256": eligible_keys.hexdigest(),
        "role_population_sha256": content.hexdigest(),
    }


def windows(db: sqlite3.Connection) -> Iterator[Window]:
    for (group,) in db.execute("SELECT DISTINCT group_key FROM examples ORDER BY group_key"):
        records = db.execute(
            "SELECT key,feature,body,history FROM examples WHERE group_key=? ORDER BY horizon LIMIT 15",
            (group,),
        ).fetchall()
        hashes = {r[3] for r in records}
        if len(records) > 14 or len(hashes) != 1:
            raise SnapshotError("campaign_score_window_population_or_history_mismatch")
        history = HistoryContext.model_validate_json(
            zlib.decompress(
                db.execute("SELECT body FROM histories WHERE hash=?", (hashes.pop(),)).fetchone()[0]
            )
        )
        decoded = [
            (
                key,
                InputRow.model_validate_json(zlib.decompress(row)),
                PhysicalForecastExample.model_validate_json(zlib.decompress(example)),
            )
            for key, row, example, _ in records
        ]
        _horizons((row for _, row, _ in decoded), history)
        yield decoded, history


def batches(db: sqlite3.Connection, size: int) -> Iterator[list[Window]]:
    iterator = windows(db)
    while batch := list(islice(iterator, size)):
        yield batch
