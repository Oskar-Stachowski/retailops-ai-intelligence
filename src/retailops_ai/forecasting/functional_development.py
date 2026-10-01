"""Immutable train/validation cache; never a campaign or an independent qualification.

Parent semantics are verified once, or inherited from an exact, independently replayed
local evidence run. Later experiments verify bytes and temporal boundaries, without
repeating the expensive semantic split replay. No holdout rows enter the cache.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import stat
import tempfile
import zlib
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal, cast

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.functional_campaign import (
    load_campaign,
    samples,
    validate_freeze,
    write_json,
)
from retailops_ai.forecasting.functional_contract import FunctionalPolicy
from retailops_ai.forecasting.functional_models import functional_code
from retailops_ai.forecasting.functional_preprocessing import (
    fit_ordered_train_samples,
    transform_values,
)
from retailops_ai.forecasting.functional_recipe import empirical_baselines
from retailops_ai.forecasting.functional_run import load_run
from retailops_ai.forecasting.manifest_contract import FoldPlan, Membership, SplitManifest
from retailops_ai.forecasting.manifest_io import iter_table, key
from retailops_ai.forecasting.manifests import feature_key, input_models, load_feature_set
from retailops_ai.forecasting.models import model_code
from retailops_ai.forecasting.preprocessing import FittedState
from retailops_ai.forecasting.quality_contract import QualityPolicy
from retailops_ai.forecasting.quality_metrics import volume_bin
from retailops_ai.forecasting.splits import load_split, verify_split
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    read_json,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

ROLES = ("train", "validation")
MAX_BYTES = 2 * 1024**3
MAX_ROWS = 1000000
MAX_ROW_BYTES = 1024**2


def development_code() -> dict[str, str]:
    return functional_code() | {
        "forecasting/" + name: hashlib.sha256(
            files("retailops_ai.forecasting").joinpath(name).read_bytes()
        ).hexdigest()
        for name in ("functional_development.py", "functional_run.py")
    }


def receipts(root: Path) -> dict[str, dict[str, Any]]:
    """Bounded byte inventory, including metadata; no symlinks or extra hidden files."""
    checked_directory(root)
    result = {}
    total = 0
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in (*dirs, *names):
            path = Path(base) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise SnapshotError("development_parent_special_file")
            if stat.S_ISREG(mode):
                relative = path.relative_to(root).as_posix()
                size, digest = file_hash(root, relative)
                result[relative] = {"size_bytes": size, "sha256": digest}
                total += size
                if len(result) > 10000 or total > 8 * 1024**3:
                    raise SnapshotError("development_parent_budget")
    return dict(sorted(result.items()))


def _parents(features: Path, split: Path) -> dict[str, Any]:
    return {"features": receipts(features), "split": receipts(split)}


def _verify_parents(
    features: Path,
    split: Path,
    expected: dict[str, Any],
    verified_run: Path | None,
    replay_receipt: Path | None,
) -> tuple[SplitManifest, dict[str, Any]]:
    if (verified_run is None) != (replay_receipt is None):
        raise SnapshotError("development_replay_requires_run_and_receipt")
    if verified_run is None or replay_receipt is None:
        manifest = verify_split(split, features)
        evidence: dict[str, Any] = {"method": "full_semantic_split_verification_once"}
    else:
        # This is reuse of a trusted local receipt, not fresh model qualification.
        report = read_json(replay_receipt.parent, replay_receipt.name)
        run = load_run(verified_run)
        campaign = load_campaign(verified_run / "campaign")
        manifest = load_split(split)
        desc = campaign["descriptor"]
        if (
            report.get("status") != "completed"
            or report.get("step") != "replayed_and_exported"
            or report.get("independent_replay") != "passed"
            or report.get("run_id") != run["run_id"]
            or report.get("campaign_id") != campaign["campaign_id"]
            or run["descriptor"]["replay_status"] != "passed"
            or _parents(verified_run / "features", verified_run / "split") != expected
            or desc["split_id"] != manifest.split_id
        ):
            raise SnapshotError("development_replayed_parent_receipt_mismatch")
        validate_freeze(
            desc["freeze"],
            features,
            split,
            FunctionalPolicy.model_validate_json(json.dumps(desc["policy"])),
        )
        evidence = {
            "method": "exact_parent_bytes_from_trusted_local_independent_replay_receipt",
            "run_id": run["run_id"],
            "campaign_id": campaign["campaign_id"],
            "receipt_sha256": file_hash(replay_receipt.parent, replay_receipt.name)[1],
            "run_manifest_sha256": file_hash(verified_run, "run_manifest.json")[1],
            "trust_boundary": "local_archival_receipt_not_external_operator_attestation",
        }
    if _parents(features, split) != expected:
        raise SnapshotError("development_parent_changed_during_verification")
    if manifest.descriptor.qualification_status != "passed":
        raise SnapshotError("development_split_not_ready")
    return manifest, evidence


def _index(db: sqlite3.Connection, features: Path, split_dir: Path, split: SplitManifest) -> None:
    page = db.execute("PRAGMA page_size").fetchone()[0]
    db.execute(f"PRAGMA max_page_count={MAX_BYTES // page}")
    db.execute("PRAGMA cache_size=-4096")
    db.execute("PRAGMA temp_store=FILE")
    db.execute(
        "CREATE TABLE members (key TEXT PRIMARY KEY,fkey BLOB,fold TEXT,role TEXT,eligible INTEGER,body BLOB)"
    )
    db.execute("CREATE INDEX member_roles ON members(fold,role,eligible,fkey)")
    db.execute("CREATE INDEX member_features ON members(fkey)")
    db.execute("CREATE TABLE features (key BLOB PRIMARY KEY,body BLOB)")
    db.execute("CREATE TABLE labels (key TEXT PRIMARY KEY,body BLOB)")
    db.execute("CREATE TABLE history (key TEXT PRIMARY KEY,body BLOB)")
    budget: Counter[str] = Counter()
    for record in iter_table(split_dir, "memberships", split.tables["memberships"], budget):
        if not isinstance(record, Membership) or record.role not in ROLES:
            continue
        db.execute(
            "INSERT INTO members VALUES (?,?,?,?,?,?)",
            (
                key(record).decode(),
                feature_key(record),
                record.fold,
                record.role,
                record.eligible,
                zlib.compress(canonical_bytes(record.model_dump(mode="json"))),
            ),
        )
    for record in iter_table(split_dir, "labels", split.tables["labels"], budget):
        if record.role not in ROLES:
            continue
        db.execute(
            "INSERT INTO labels VALUES (?,?)",
            (key(record).decode(), zlib.compress(canonical_bytes(record.model_dump(mode="json")))),
        )
    for row in input_models(features, "features"):
        if not isinstance(row, InputRow):
            raise SnapshotError("development_feature_type")
        if db.execute("SELECT 1 FROM members WHERE fkey=? LIMIT 1", (feature_key(row),)).fetchone():
            db.execute(
                "INSERT INTO features VALUES (?,?)",
                (feature_key(row), zlib.compress(canonical_bytes(row.model_dump(mode="json")))),
            )
            db.execute(
                "INSERT OR IGNORE INTO history VALUES (?,NULL)", (row.history_context_sha256,)
            )
    for history in input_models(features, "history"):
        if not isinstance(history, HistoryContext):
            raise SnapshotError("development_history_type")
        db.execute(
            "UPDATE history SET body=? WHERE key=?",
            (
                zlib.compress(canonical_bytes(history.model_dump(mode="json"))),
                history.content_sha256(),
            ),
        )
    if (
        db.execute("SELECT COUNT(*) FROM history WHERE body IS NULL").fetchone()[0]
        or db.execute(
            "SELECT COUNT(*) FROM members m LEFT JOIN features f ON f.key=m.fkey LEFT JOIN labels l ON l.key=m.key WHERE f.key IS NULL OR l.key IS NULL"
        ).fetchone()[0]
    ):
        raise SnapshotError("development_parent_keys_incomplete")
    db.commit()


def validate_row(row: dict[str, Any], fold: FoldPlan, columns: int) -> None:
    """Every cached row is still checked at consumption, including excluded rows."""
    role = row["role"]
    origin = datetime.fromisoformat(row["origin"])
    cutoff = fold.label_cutoff(cast(Literal["train", "validation"], role))
    row_key = json.loads(row["key"])
    expected_key = [
        fold.name,
        role,
        row["product_id"],
        row["selling_location_id"],
        row["channel"],
        row["target_date"],
    ]
    if (
        role not in ROLES
        or row["fold"] != fold.name
        or fold.role(origin.date()) != role
        or origin.utcoffset() is None
        or row["label_cutoff"] != cutoff.isoformat()
        or origin > cutoff
        or len(row_key) != 7
        or [*row_key[:2], *row_key[3:]] != expected_key
        or datetime.fromisoformat(row_key[2]) != origin
    ):
        raise SnapshotError("development_only_train_validation_origin_binding")
    available = row["label_available_at"]
    feature_available = row["feature_available_at"]
    if feature_available is not None and datetime.fromisoformat(feature_available) > origin:
        raise SnapshotError("development_future_feature")
    if row["eligible"]:
        if (
            row["reasons"]
            or type(row["actual"]) is not int
            or row["actual"] < 0
            or available is None
            or datetime.fromisoformat(available).utcoffset() is None
            or datetime.fromisoformat(available) > cutoff
        ):
            raise SnapshotError("development_label_not_available_at_role_cutoff")
    elif row["actual"] is not None or available is not None or not row["reasons"]:
        raise SnapshotError("development_excluded_label_must_be_absent")
    if len(row["vector"]) != columns or any(
        type(v) not in (int, float) or not math.isfinite(v) for v in row["vector"]
    ):
        raise SnapshotError("development_invalid_vector")


def build_development_cache(
    features: Path,
    split_dir: Path,
    output: Path,
    *,
    verified_run: Path | None = None,
    replay_receipt: Path | None = None,
) -> Path:
    """One immutable cache containing every train/validation membership, no model fits."""
    for parent in (features, split_dir):
        if output.absolute().is_relative_to(parent.absolute()):
            raise SnapshotError("development_output_inside_parent")
    output.mkdir(parents=True, exist_ok=True)
    checked_directory(output)
    if (
        min(shutil.disk_usage(output).free, shutil.disk_usage(tempfile.gettempdir()).free)
        < 16 * 1024**3
    ):
        raise SnapshotError("development_parent_verification_space_budget")
    parent_receipts = _parents(features, split_dir)
    split, verification = _verify_parents(
        features, split_dir, parent_receipts, verified_run, replay_receipt
    )
    feature = load_feature_set(features)
    with tempfile.TemporaryDirectory(prefix=".development-", dir=output) as temporary:
        root = Path(temporary)
        with tempfile.TemporaryDirectory(prefix="forecast-development-index-") as index:
            db = sqlite3.connect(Path(index) / "rows.sqlite")
            try:
                _index(db, features, split_dir, split)

                @lru_cache(maxsize=128)
                def history(digest: str) -> HistoryContext:
                    raw = db.execute("SELECT body FROM history WHERE key=?", (digest,)).fetchone()
                    return HistoryContext.model_validate_json(zlib.decompress(raw[0]))

                metadata = {}
                counts: Counter[str] = Counter()
                size = 0
                for fold in split.descriptor.resolved_policy.folds:
                    state = fit_ordered_train_samples(
                        ((r, m) for r, m, _ in samples(db, fold, "train", True)),
                        fold=fold,
                        policy=feature.descriptor.resolved_policy,
                        feature_set_id=feature.feature_set_id,
                        split_id=split.split_id,
                    )
                    state_name = f"preprocessing/{fold.name}.json"
                    (root / "preprocessing").mkdir(exist_ok=True)
                    write_json(root / state_name, state.model_dump(mode="json"))
                    metadata[fold.name] = {
                        "plan": fold.model_dump(mode="json"),
                        "preprocessing": state_name,
                        "output_columns": [*state.descriptor.output_columns, "horizon_days"],
                    }
                    for role in ROLES:
                        name = f"rows/{fold.name}/{role}.jsonl"
                        (root / name).parent.mkdir(parents=True, exist_ok=True)
                        with (root / name).open("xb") as stream:
                            for row, member, label in samples(db, fold, role):
                                values = {v.name: v.value for v in row.values}
                                points, bands = empirical_baselines(
                                    row, history(row.history_context_sha256)
                                )
                                known = [r.available_at for v in row.values for r in v.references]
                                known += [
                                    v.source_available_at
                                    for v in row.values
                                    if v.source_available_at is not None
                                ]
                                record = {
                                    "key": key(member).decode(),
                                    "fold": fold.name,
                                    "role": role,
                                    "origin": row.forecast_origin.isoformat(),
                                    "target_date": row.target_date.isoformat(),
                                    "product_id": row.product_id,
                                    "selling_location_id": row.selling_location_id,
                                    "channel": row.channel,
                                    "horizon": row.horizon_days,
                                    "category": str(values["category_id"]),
                                    "volume": volume_bin(
                                        cast(float | None, values["rolling_mean_28"]),
                                        QualityPolicy(),
                                    ),
                                    "eligible": member.eligible,
                                    "reasons": list(member.reasons),
                                    "actual": label.observed_sales_units
                                    if member.eligible
                                    else None,
                                    "label_available_at": label.label_available_at.isoformat()
                                    if member.eligible and label.label_available_at
                                    else None,
                                    "label_cutoff": fold.label_cutoff(
                                        cast(Literal["train", "validation"], role)
                                    ).isoformat(),
                                    "feature_available_at": max(known).isoformat()
                                    if known
                                    else None,
                                    "feature_values": values,
                                    "vector": [
                                        *transform_values(values, state),
                                        float(row.horizon_days),
                                    ],
                                    "baseline_points": points,
                                    "baseline_bands": bands,
                                    "history_context_sha256": row.history_context_sha256,
                                }
                                validate_row(
                                    record, fold, len(metadata[fold.name]["output_columns"])
                                )
                                raw = canonical_bytes(record) + b"\n"
                                size += len(raw)
                                counts[fold.name + ":" + role] += 1
                                counts["rows"] += 1
                                counts["eligible" if member.eligible else "excluded"] += 1
                                if (
                                    size > MAX_BYTES
                                    or len(raw) > MAX_ROW_BYTES
                                    or counts["rows"] > MAX_ROWS
                                ):
                                    raise SnapshotError("development_cache_budget")
                                stream.write(raw)
            finally:
                db.close()
        if _parents(features, split_dir) != parent_receipts:
            raise SnapshotError("development_parent_changed_during_cache_build")
        descriptor = {
            "version": "forecast-functional-development-cache-1.0.0",
            "purpose": "development_only_not_independent_qualification",
            "model_status": "not_ready",
            "roles": list(ROLES),
            "holdout_rows": 0,
            "model_fits": 0,
            "feature_set_id": feature.feature_set_id,
            "split_id": split.split_id,
            "parent": feature.descriptor.parent.model_dump(mode="json"),
            "parent_files": parent_receipts,
            "parent_verification": verification,
            "code": development_code(),
            "environment": model_code().model_dump(mode="json"),
            "folds": metadata,
            "counts": dict(counts),
            "files": receipts(root),
        }
        manifest: dict[str, Any] = {
            "cache_id": "forecast-development-sha256-" + canonical_sha256(descriptor),
            "descriptor": descriptor,
            "built_at": datetime.now(UTC).isoformat(),
        }
        write_json(root / "development_manifest.json", manifest)
        fsync_tree(root)
        destination = output / str(manifest["cache_id"])
        publish_noreplace(root, destination)
    return destination


def load_development_cache(root: Path, features: Path, split_dir: Path) -> dict[str, Any]:
    """Byte verification only; the immutable receipt carries the earlier semantic replay."""
    manifest = read_json(root, "development_manifest.json")
    desc = manifest["descriptor"]
    if (
        manifest["cache_id"] != "forecast-development-sha256-" + canonical_sha256(desc)
        or desc["code"] != development_code()
        or desc["environment"] != model_code().model_dump(mode="json")
        or desc["purpose"] != "development_only_not_independent_qualification"
        or desc["model_status"] != "not_ready"
        or desc["roles"] != list(ROLES)
        or desc["holdout_rows"] != 0
        or desc["model_fits"] != 0
        or desc["parent_files"] != _parents(features, split_dir)
    ):
        raise SnapshotError("development_cache_identity_code_or_parent_mismatch")
    inventory(root, {"development_manifest.json", *desc["files"]})
    for name, receipt in desc["files"].items():
        if file_hash(root, name) != (receipt["size_bytes"], receipt["sha256"]):
            raise SnapshotError("development_cache_file_checksum")
    return manifest


def iter_development_rows(
    root: Path,
    manifest: dict[str, Any],
    fold_name: str,
    role: Literal["train", "validation"],
    *,
    eligible_only: bool = False,
) -> Iterator[dict[str, Any]]:
    """Consume one verified cache partition; each read also rechecks that file's checksum."""
    if role not in ROLES:
        raise SnapshotError("development_holdout_role_forbidden")
    meta = manifest["descriptor"]["folds"][fold_name]
    fold = FoldPlan.model_validate_json(json.dumps(meta["plan"]))
    state = FittedState.model_validate_json(json.dumps(read_json(root, meta["preprocessing"])))
    if state.descriptor.fold != fold:
        raise SnapshotError("development_preprocessing_fold_binding")
    name = f"rows/{fold_name}/{role}.jsonl"
    expected = manifest["descriptor"]["files"][name]
    if file_hash(root, name) != (expected["size_bytes"], expected["sha256"]):
        raise SnapshotError("development_cache_file_checksum")
    with regular_file(root, name) as stream:
        for line in stream:
            if len(line) > MAX_ROW_BYTES:
                raise SnapshotError("development_cache_row_budget")
            row = decode_json(line)
            validate_row(row, fold, len(meta["output_columns"]))
            if not eligible_only or row["eligible"]:
                yield row
