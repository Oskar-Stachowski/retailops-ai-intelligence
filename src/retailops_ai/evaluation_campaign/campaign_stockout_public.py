"""Complete public stockout preparation with native retrospective upstream.

The enclosing campaign reserves and measures this operation before entry.
Neither private labels nor a learned model are read. Publication follows every
parent recheck; an incomplete operation never returns a usable artifact.
"""

import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_stockout_parent import CampaignStockoutPublicParent
from retailops_ai.evaluation_campaign.campaign_stockout_public_contract import (
    CampaignStockoutPublicPlan,
    CampaignStockoutPublicRow,
)
from retailops_ai.evaluation_campaign.campaign_stockout_upstream_facts import (
    CampaignStockoutUpstreamFacts,
    CampaignStockoutUpstreamIndex,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    inventory,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.importer import write_private
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace
from retailops_ai.stockout.upstream import model_version, upstream_point


def _budget(indices: list[tuple[Path, sqlite3.Connection]], maximum: int) -> int:
    size = 0
    for path, db in indices:
        info = path.stat()
        logical = (
            db.execute("PRAGMA page_count").fetchone()[0]
            * db.execute("PRAGMA page_size").fetchone()[0]
        )
        size += max(info.st_size, info.st_blocks * 512, logical)
        size += sum(p.stat().st_size for p in path.parent.glob(path.name + "-*") if p.is_file())
    if size > maximum:
        raise SnapshotError("campaign_stockout_public_combined_index_budget")
    return size


def _row(raw: bytes, checksum: str, key: tuple[str, str, str], maximum: int) -> dict[str, Any]:
    if len(raw) > maximum or hashlib.sha256(raw).hexdigest() != checksum:
        raise SnapshotError("campaign_stockout_public_private_row_changed")
    row = decode_json(raw, limit=maximum)
    if (row["product_id"], row["stock_location_id"], row["as_of"]) != key:
        raise SnapshotError("campaign_stockout_public_private_key_changed")
    return row


def prepare_public_stockout(
    snapshot: Path, curated: Path, target: Path, plan: CampaignStockoutPublicPlan
) -> dict[str, Any]:
    plan = CampaignStockoutPublicPlan.model_validate_json(plan.model_dump_json())
    snapshot, curated = checked_directory(snapshot), checked_directory(curated)
    parent = checked_directory(target.absolute().parent)
    target = parent / target.name
    if (
        target.exists()
        or target.is_symlink()
        or any(parent == p or parent.is_relative_to(p) for p in (snapshot, curated))
    ):
        raise SnapshotError("campaign_stockout_public_new_separate_output_required")
    with tempfile.TemporaryDirectory(prefix=".ai09-stockout-public-", dir=parent) as temporary:
        root = Path(temporary)
        bundle = root / "bundle"
        bundle.mkdir(mode=0o700)
        db_path = root / "points.sqlite"
        db_path.touch(mode=0o600)
        source = CampaignStockoutPublicParent(snapshot, curated, plan.parent, root)
        with closing(sqlite3.connect(db_path)) as db, source:
            db.execute("PRAGMA page_size=4096")
            db.execute("PRAGMA cache_size=-8192")
            db.execute("PRAGMA temp_store=FILE")
            db.execute("PRAGMA mmap_size=0")
            db.execute("PRAGMA max_page_count=" + str(plan.max_index_bytes // 4096))
            db.execute(
                "CREATE TABLE inputs(product TEXT,stock TEXT,origin TEXT,body BLOB,checksum TEXT,PRIMARY KEY(product,stock,origin))"
            )
            db.execute(
                "CREATE TABLE predictions(product TEXT,stock TEXT,origin TEXT,body BLOB,checksum TEXT,PRIMARY KEY(product,stock,origin))"
            )
            count = peak = 0
            input_trace = hashlib.sha256()
            indices = [(db_path, db), (source._facts.path, source._facts._connection())]
            for projected in source.rows():
                feature = projected["feature"]
                row = dict(
                    product_id=feature["product_id"],
                    stock_location_id=feature["stock_location_id"],
                    as_of=feature["as_of"],
                    status=feature["status"],
                    reason=feature["reason"],
                    category_id=projected["category_id"],
                    category_source_record_sha256=projected["category_source_record_sha256"],
                    values=feature["values"],
                    feature_sha256=canonical_sha256(feature),
                )
                raw = canonical_bytes(row)
                if len(raw) > plan.max_record_bytes:
                    raise SnapshotError("campaign_stockout_public_record_budget")
                db.execute(
                    "INSERT INTO inputs VALUES(?,?,?,?,?)",
                    (
                        row["product_id"],
                        row["stock_location_id"],
                        row["as_of"],
                        raw,
                        hashlib.sha256(raw).hexdigest(),
                    ),
                )
                input_trace.update(raw + b"\n")
                count += 1
                if count % 256 == 0:
                    db.commit()
                    peak = max(peak, _budget(indices, plan.max_index_bytes))
            db.commit()
            peak = max(peak, _budget(indices, plan.max_index_bytes))
            upstream_trace = hashlib.sha256(b"[")
            with CampaignStockoutUpstreamFacts(
                source._replay.curated,
                scope=source._facts.scope,
                policy=plan.parent.storage,
                scratch=root,
            ) as facts:
                index = CampaignStockoutUpstreamIndex(facts)
                indices.append((facts.path, facts._connection()))
                version = model_version(plan.upstream)
                upstream_count = 0
                for product, stock, stamp, raw, checksum in db.execute(
                    "SELECT product,stock,origin,body,checksum FROM inputs ORDER BY origin,product,stock"
                ):
                    row = _row(raw, checksum, (product, stock, stamp), plan.max_record_bytes)
                    origin = datetime.fromisoformat(stamp)
                    records = index.known_series(product, stock, end_of_day(origin.date()))
                    point = upstream_point(
                        records,
                        product=product,
                        stock=stock,
                        as_of=origin,
                        policy=plan.upstream,
                        version=version,
                    )
                    del records
                    state = point.model_dump(mode="json")
                    if upstream_count:
                        upstream_trace.update(b",")
                    upstream_trace.update(canonical_bytes(state))
                    units, quantity = point.forecast_units_7d, row["values"]["available_qty"]
                    row["values"].update(
                        forecast_units_7d=units,
                        forecast_days_of_supply=7 * quantity / units
                        if quantity is not None and units
                        else None,
                        forecast_unavailable=int(point.status != "available"),
                    )
                    row["upstream_sha256"] = canonical_sha256(state)
                    validated = CampaignStockoutPublicRow.model_validate_json(canonical_bytes(row))
                    raw = canonical_bytes(validated.model_dump(mode="json"))
                    if len(raw) + 1 > plan.max_record_bytes:
                        raise SnapshotError("campaign_stockout_public_record_budget")
                    db.execute(
                        "INSERT INTO predictions VALUES(?,?,?,?,?)",
                        (product, stock, stamp, raw, hashlib.sha256(raw).hexdigest()),
                    )
                    upstream_count += 1
                    if upstream_count % 256 == 0:
                        db.commit()
                        peak = max(peak, _budget(indices, plan.max_index_bytes))
                upstream_trace.update(b"]")
                db.commit()
                if (
                    count != upstream_count
                    or db.execute("SELECT COUNT(*) FROM inputs").fetchone()[0] != count
                ):
                    raise SnapshotError("campaign_stockout_public_full_upstream_population")
                # Resealing an individual private row must not change the original feature population.
                observed = hashlib.sha256()
                for product, stock, stamp, raw, checksum in db.execute(
                    "SELECT product,stock,origin,body,checksum FROM inputs ORDER BY product,stock,origin"
                ):
                    _row(raw, checksum, (product, stock, stamp), plan.max_record_bytes)
                    observed.update(raw + b"\n")
                if observed.digest() != input_trace.digest():
                    raise SnapshotError("campaign_stockout_public_private_input_population_changed")
                index.check_parent()
                peak = max(peak, _budget(indices, plan.max_index_bytes))
                upstream_seal, upstream_stats = deepcopy(index.seal), deepcopy(index.stats)
            digest, keys = hashlib.sha256(), hashlib.sha256()
            size = written = 0
            output = bundle / "rows.jsonl"
            output.touch(mode=0o600)
            with output.open("wb") as stream:
                for product, stock, stamp, raw, checksum in db.execute(
                    "SELECT product,stock,origin,body,checksum FROM predictions ORDER BY product,stock,origin"
                ):
                    _row(raw, checksum, (product, stock, stamp), plan.max_record_bytes)
                    line = raw + b"\n"
                    size += len(line)
                    written += 1
                    if size > plan.max_artifact_bytes:
                        raise SnapshotError("campaign_stockout_public_artifact_budget")
                    stream.write(line)
                    digest.update(line)
                    keys.update(canonical_bytes([product, stock, stamp]) + b"\n")
            if written != count:
                raise SnapshotError("campaign_stockout_public_incomplete_output")
        receipt = source.receipt()
        if receipt["origins"] != count or receipt["keys_sha256"] != keys.hexdigest():
            raise SnapshotError("campaign_stockout_public_original_feature_keys_mismatch")
        descriptor = dict(
            version="ai09-stockout-full-public-descriptor-1.0.0",
            plan=plan.model_dump(mode="json"),
            feature_parent_receipt=receipt,
            upstream_input_seal=upstream_seal,
            upstream_native_order="as_of_product_stock",
            upstream_native_points_sha256=upstream_trace.hexdigest(),
            upstream_model_version=version,
            upstream_stats=upstream_stats,
            origins=count,
            keys_sha256=keys.hexdigest(),
            compact_feature_rows_sha256=input_trace.hexdigest(),
            rows_bytes=size,
            rows_sha256=digest.hexdigest(),
            maximum_combined_index_bytes=peak,
        )
        manifest: dict[str, Any] = dict(
            dataset_id="ai09-stockout-public-sha256-" + canonical_sha256(descriptor),
            descriptor=descriptor,
            private_labels_read=False,
            mature_role_membership_verified=False,
            journal_authorization_proven=False,
            quality_qualified=False,
            stage_ready=False,
        )
        body = canonical_bytes(manifest) + b"\n"
        if size + len(body) > plan.max_artifact_bytes:
            raise SnapshotError("campaign_stockout_public_artifact_budget")
        write_private(bundle / "manifest.json", body)
        fsync_tree(bundle)
        verify_public_stockout(
            bundle, expected_dataset_id=manifest["dataset_id"], expected_plan=plan
        )
        publish_noreplace(bundle, target)
        return manifest


def verify_public_stockout(
    root: Path, *, expected_dataset_id: str, expected_plan: CampaignStockoutPublicPlan
) -> dict[str, Any]:
    """Complete sealed artifact check, not independent Source/journal authorization."""
    expected_plan = CampaignStockoutPublicPlan.model_validate_json(expected_plan.model_dump_json())
    inventory(root, {"manifest.json", "rows.jsonl"})
    original = read_bytes(root, "manifest.json")
    manifest = decode_json(original)
    descriptor = manifest["descriptor"]
    if (
        original != canonical_bytes(manifest) + b"\n"
        or set(manifest)
        != {
            "dataset_id",
            "descriptor",
            "private_labels_read",
            "mature_role_membership_verified",
            "journal_authorization_proven",
            "quality_qualified",
            "stage_ready",
        }
        or any(
            manifest[k] is not False
            for k in (
                "private_labels_read",
                "mature_role_membership_verified",
                "journal_authorization_proven",
                "quality_qualified",
                "stage_ready",
            )
        )
        or manifest["dataset_id"] != expected_dataset_id
        or expected_dataset_id != "ai09-stockout-public-sha256-" + canonical_sha256(descriptor)
        or descriptor["version"] != "ai09-stockout-full-public-descriptor-1.0.0"
        or descriptor["plan"] != expected_plan.model_dump(mode="json")
    ):
        raise SnapshotError("campaign_stockout_public_frozen_manifest_mismatch")
    digest, keys = hashlib.sha256(), hashlib.sha256()
    count = size = 0
    previous = None
    with regular_file(root, "rows.jsonl") as stream:
        before = os.fstat(stream.fileno())
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        while raw := stream.readline(expected_plan.max_record_bytes + 1):
            if len(raw) > expected_plan.max_record_bytes:
                raise SnapshotError("campaign_stockout_public_record_budget")
            decoded = decode_json(raw, limit=expected_plan.max_record_bytes)
            row = CampaignStockoutPublicRow.model_validate_json(canonical_bytes(decoded))
            if raw != canonical_bytes(row.model_dump(mode="json")) + b"\n":
                raise SnapshotError("campaign_stockout_public_noncanonical_row")
            key = row.product_id, row.stock_location_id, row.as_of
            if previous is not None and key <= previous:
                raise SnapshotError("campaign_stockout_public_duplicate_or_unsorted_key")
            previous = key
            count += 1
            size += len(raw)
            if (
                count > expected_plan.parent.max_origins
                or size + len(original) > expected_plan.max_artifact_bytes
            ):
                raise SnapshotError("campaign_stockout_public_full_population_budget")
            digest.update(raw)
            keys.update(
                canonical_bytes(
                    [row.product_id, row.stock_location_id, row.model_dump(mode="json")["as_of"]]
                )
                + b"\n"
            )
        for after in (os.fstat(stream.fileno()), (root / "rows.jsonl").stat()):
            if identity != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise SnapshotError("campaign_stockout_public_changed_during_verification")
    if (
        not count
        or count != descriptor["origins"]
        or size != descriptor["rows_bytes"]
        or digest.hexdigest() != descriptor["rows_sha256"]
        or keys.hexdigest() != descriptor["keys_sha256"]
        or read_bytes(root, "manifest.json") != original
    ):
        raise SnapshotError("campaign_stockout_public_complete_rows_mismatch")
    inventory(root, {"manifest.json", "rows.jsonl"})
    return manifest
