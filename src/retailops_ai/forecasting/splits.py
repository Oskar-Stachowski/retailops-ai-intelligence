"""Content-bound development split, mature labels and shared coverage membership."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from retailops_ai.curated.contract import columns_for, decoded
from retailops_ai.data_contracts.common import DateWindow, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.calendar import load_calendar
from retailops_ai.forecasting.features import latest
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.features_store import source_index
from retailops_ai.forecasting.manifest_contract import (
    FeaturePolicy,
    FoldPlan,
    LabelDescriptor,
    LabelPoint,
    Membership,
    Reason,
    SplitDescriptor,
    SplitManifest,
    SplitPolicy,
)
from retailops_ai.forecasting.manifest_io import TableWriter, code_pin, dump, iter_table, key
from retailops_ai.forecasting.manifests import feature_key, input_models, verify_feature_set
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    inventory,
    read_bytes,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

FORECAST_FIELDS = (
    "product_id",
    "selling_location_id",
    "channel",
    "forecast_origin",
    "business_timezone",
    "cutoff_policy",
    "target_date",
    "horizon_days",
)


def default_split(calendar: Any) -> SplitPolicy:
    window = calendar.descriptor.origin_window
    count = (window.end - window.start).days + 1
    if count < 51:
        raise SnapshotError("forecast_split_requires_at_least_51_origins")
    train = DateWindow(start=window.start, end=window.end - timedelta(days=50))
    validation = DateWindow(
        start=train.end + timedelta(days=16), end=train.end + timedelta(days=25)
    )
    holdout = DateWindow(start=validation.end + timedelta(days=16), end=window.end)
    return SplitPolicy(
        folds=(
            FoldPlan(
                name="development-v1",
                train=train,
                validation=validation,
                development_holdout=holdout,
                training_cutoff=end_of_day(validation.start - timedelta(days=1)),
                selection_cutoff=end_of_day(holdout.start - timedelta(days=1)),
                evaluation_cutoff=end_of_day(holdout.end + timedelta(days=15)),
            ),
        )
    )


def label_point(row: InputRow, fold: FoldPlan, candidates: list[dict[str, Any]]) -> LabelPoint:
    role = fold.role(row.forecast_origin.date())
    if role == "purged":
        raise SnapshotError("purged_origin_has_no_label")
    cutoff = fold.label_cutoff(role)
    known = [
        r
        for r in candidates
        if r["curated_available_at"] is not None
        and r["curated_available_at"] <= cutoff
        and r["business_date"] == row.target_date
        and tuple(r[k] for k in ("product_id", "selling_location_id", "channel"))
        == (row.product_id, row.selling_location_id, row.channel)
    ]
    selected = latest(known, ("business_date", "product_id", "selling_location_id", "channel"))
    fact = selected[0] if selected else None
    complete = (
        fact is not None
        and fact.get("source_data_complete", True) is True
        and fact.get("quality_status", "valid") == "valid"
        and fact["observed_units"] is not None
        and fact["observation_status"] in {"observed_positive", "observed_zero", "closed"}
    )
    return LabelPoint(
        **{k: getattr(row, k) for k in FORECAST_FIELDS},
        fold=fold.name,
        role=role,
        knowledge_cutoff=cutoff,
        status="eligible" if complete else "censored",
        observed_sales_units=fact["observed_units"] if complete and fact is not None else None,
        label_available_at=fact["curated_available_at"] if complete and fact is not None else None,
        source_record_sha256=fact["source_record_sha256"]
        if complete and fact is not None
        else None,
        source_record_id=fact["id"] if complete and fact is not None else None,
        version=fact["version"] if complete and fact is not None else None,
        reason=None
        if complete
        else "missing_or_unavailable"
        if fact is None
        else "incomplete_source",
    )


def qualify(
    row: InputRow,
    history: HistoryContext,
    fold: FoldPlan,
    policy: FeaturePolicy,
    label: LabelPoint | None,
) -> Membership:
    role = fold.role(row.forecast_origin.date())
    reasons: list[Reason] = []
    if role == "purged":
        reasons.append("purged_origin")
    else:
        if (
            len(history.points) < policy.minimum_active_history_days
            or row.history_known_days < policy.minimum_known_history_days
        ):
            reasons.append("insufficient_history")
        known_days = [p.business_date for p in history.points if p.status != "missing"]
        if (
            known_days
            and (row.forecast_origin.date() - max(known_days)).days
            > policy.maximum_observation_age_days
        ) or (not known_days and len(history.points) >= policy.minimum_active_history_days):
            reasons.append("stale_history")
        opening = next(v.value for v in row.values if v.name == "target_location_open")
        if opening is None:
            reasons.append("unknown_calendar")
        elif opening is False:
            reasons.append("closed_target")
        if label is None or label.status != "eligible":
            reasons.append("censored_label")
    return Membership(
        **{k: getattr(row, k) for k in FORECAST_FIELDS},
        fold=fold.name,
        role=role,
        eligible=not reasons,
        reasons=tuple(reasons),
        label_content_sha256=canonical_sha256(label.model_dump(mode="json"))
        if label is not None
        else None,
    )


def history_index(db: sqlite3.Connection, feature_dir: Path) -> Any:
    db.execute("CREATE TABLE histories (hash TEXT PRIMARY KEY, body BLOB)")
    for context in input_models(feature_dir, "history"):
        if not isinstance(context, HistoryContext):
            raise SnapshotError("forecast_split_history_schema_mismatch")
        db.execute(
            "INSERT INTO histories VALUES (?,?)",
            (context.content_sha256(), canonical_bytes(context.model_dump(mode="json"))),
        )

    @lru_cache(maxsize=128)
    def get_context(identifier: str) -> HistoryContext:
        found = db.execute("SELECT body FROM histories WHERE hash=?", (identifier,)).fetchone()
        if found is None:
            raise SnapshotError("forecast_split_missing_history_context")
        return HistoryContext.model_validate_json(found[0])

    return get_context


def fold_bounds(policy: SplitPolicy, calendar: Any) -> None:
    window = calendar.descriptor.origin_window
    if any(
        f.train.start < window.start or f.development_holdout.end > window.end for f in policy.folds
    ):
        raise SnapshotError("forecast_split_window_outside_calendar")


def count_membership(counts: Counter[str], row: Membership) -> None:
    counts[row.fold + ":" + row.role + ":total"] += 1
    counts[row.fold + ":" + row.role + ":eligible"] += int(row.eligible)
    for reason in row.reasons:
        counts[reason] += 1


def build_split(
    feature_dir: Path, curated_dir: Path, output_root: Path, policy: SplitPolicy | None = None
) -> Path:
    output_root = output_root.absolute()
    feature_manifest = verify_feature_set(feature_dir)
    calendar = load_calendar(feature_dir / "inputs/calendar_manifest.json")
    policy = (
        default_split(calendar)
        if policy is None
        else SplitPolicy.model_validate_json(policy.model_dump_json())
    )
    fold_bounds(policy, calendar)
    if any(output_root.absolute().is_relative_to(p.absolute()) for p in (feature_dir, curated_dir)):
        raise SnapshotError("forecast_split_output_inside_input")
    output_root.mkdir(parents=True, exist_ok=True)
    checked_directory(output_root)
    with tempfile.TemporaryDirectory(prefix=".split-", dir=output_root) as temporary:
        root = Path(temporary)
        with tempfile.TemporaryDirectory(prefix="forecast-split-index-") as index:
            db = sqlite3.connect(Path(index) / "rows.sqlite")
            try:
                db.execute("PRAGMA cache_size=-4096")
                db.execute("PRAGMA temp_store=FILE")
                db.execute(
                    "CREATE TABLE rows (kind TEXT, key BLOB, body BLOB, PRIMARY KEY(kind,key))"
                )
                get_history = history_index(db, feature_dir)
                budget: Counter[str] = Counter()
                writers = {n: TableWriter(root, n, db, budget) for n in ("labels", "memberships")}
                counts: Counter[str] = Counter()
                with source_index(curated_dir, calendar) as source:

                    @lru_cache(maxsize=4096)
                    def observations(
                        day: str, product: str, location: str, channel: str
                    ) -> list[dict[str, Any]]:
                        return [
                            r
                            for (body,) in source.db.execute(
                                "SELECT body FROM source WHERE kind='daily_demand_versions' AND business_date=? ORDER BY identifier",
                                (day,),
                            )
                            if (r := decoded(body, columns_for("daily_demand_versions")))[
                                "product_id"
                            ]
                            == product
                            and r["selling_location_id"] == location
                            and r["channel"] == channel
                        ]

                    for row in input_models(feature_dir, "features"):
                        if not isinstance(row, InputRow):
                            raise SnapshotError("forecast_split_feature_schema_mismatch")
                        history = get_history(row.history_context_sha256)
                        for fold in policy.folds:
                            label = (
                                None
                                if fold.role(row.forecast_origin.date()) == "purged"
                                else label_point(
                                    row,
                                    fold,
                                    observations(
                                        row.target_date.isoformat(),
                                        row.product_id,
                                        row.selling_location_id,
                                        row.channel,
                                    ),
                                )
                            )
                            if label is not None:
                                writers["labels"].add(label)
                            membership = qualify(
                                row,
                                history,
                                fold,
                                feature_manifest.descriptor.resolved_policy,
                                label,
                            )
                            writers["memberships"].add(membership)
                            count_membership(counts, membership)
                tables = {n: writer.summary() for n, writer in writers.items()}
            except sqlite3.IntegrityError as exc:
                raise SnapshotError("duplicate_forecast_split_record") from exc
            finally:
                db.close()
        code = code_pin()
        label_desc = LabelDescriptor(
            parent=feature_manifest.descriptor.parent,
            feature_set_id=feature_manifest.feature_set_id,
            policy=policy,
            code=code,
            content_sha256=tables["labels"].content_sha256,
            row_count=tables["labels"].row_count,
        )
        ready = not counts["stale_history"] and all(
            counts[f.name + ":" + role + ":eligible"] >= policy.minimum_eligible_rows_per_role
            for f in policy.folds
            for role in ("train", "validation", "development_holdout")
        )
        descriptor = SplitDescriptor(
            feature_set_id=feature_manifest.feature_set_id,
            label_dataset_id="labels-sha256-"
            + canonical_sha256(label_desc.model_dump(mode="json")),
            parent=feature_manifest.descriptor.parent,
            requested_policy=policy,
            resolved_policy=policy,
            feature_policy=feature_manifest.descriptor.resolved_policy,
            code=code,
            content_sha256=tables["memberships"].content_sha256,
            row_count=tables["memberships"].row_count,
            counts=dict(counts),
            qualification_status="passed" if ready else "not_ready",
        )
        manifest = SplitManifest(
            split_id="split-sha256-" + canonical_sha256(descriptor.model_dump(mode="json")),
            descriptor=descriptor,
            labels=label_desc,
            tables=tables,
            generated_at=datetime.now(UTC),
        )
        dump(root / "split_manifest.json", manifest)
        verify_split(root, feature_dir)
        fsync_tree(root)
        destination = output_root / manifest.split_id
        try:
            publish_noreplace(root, destination)
        except FileExistsError:
            if verify_split(destination, feature_dir).descriptor != descriptor:
                raise SnapshotError("forecast_split_publication_conflict") from None
        return destination


def load_split(root: Path) -> SplitManifest:
    raw = read_bytes(root, "split_manifest.json")
    decode_json(raw)
    return SplitManifest.model_validate_json(raw)


def verify_split(root: Path, feature_dir: Path) -> SplitManifest:
    checked_directory(root)
    manifest = load_split(root)
    features = verify_feature_set(feature_dir)
    if (
        manifest.descriptor.feature_set_id != features.feature_set_id
        or manifest.descriptor.parent != features.descriptor.parent
        or manifest.descriptor.feature_policy != features.descriptor.resolved_policy
    ):
        raise SnapshotError("forecast_split_feature_parent_mismatch")
    calendar = load_calendar(feature_dir / "inputs/calendar_manifest.json")
    fold_bounds(manifest.descriptor.resolved_policy, calendar)
    names = {"split_manifest.json"}
    with tempfile.TemporaryDirectory(prefix="forecast-split-verify-") as temporary:
        db = sqlite3.connect(Path(temporary) / "rows.sqlite")
        try:
            db.execute("PRAGMA cache_size=-4096")
            db.execute("PRAGMA temp_store=FILE")
            db.execute("CREATE TABLE rows (kind TEXT, key BLOB, body BLOB, PRIMARY KEY(kind,key))")
            db.execute("CREATE TABLE features (key BLOB PRIMARY KEY, body BLOB)")
            for row in input_models(feature_dir, "features"):
                db.execute(
                    "INSERT INTO features VALUES (?,?)",
                    (feature_key(row), canonical_bytes(row.model_dump(mode="json"))),
                )
            get_history = history_index(db, feature_dir)
            budget: Counter[str] = Counter()
            for name, spec in manifest.tables.items():
                names.update(r.path for r in spec.files)
                count = 0
                for record in iter_table(root, name, spec, budget):
                    db.execute(
                        "INSERT INTO rows VALUES (?,?,?)",
                        (name, key(record), canonical_bytes(record.model_dump(mode="json"))),
                    )
                    count += 1
                digest = hashlib.sha256()
                for (body,) in db.execute(
                    "SELECT body FROM rows WHERE kind=? ORDER BY key", (name,)
                ):
                    digest.update(body + b"\n")
                if count != spec.row_count or digest.hexdigest() != spec.content_sha256:
                    raise SnapshotError("forecast_split_logical_content_mismatch")
            counts: Counter[str] = Counter()
            label_count = 0
            folds = {f.name: f for f in manifest.descriptor.resolved_policy.folds}
            for (body,) in db.execute("SELECT body FROM rows WHERE kind='memberships'"):
                membership = Membership.model_validate_json(body)
                found = db.execute(
                    "SELECT body FROM features WHERE key=?", (feature_key(membership),)
                ).fetchone()
                if found is None or membership.fold not in folds:
                    raise SnapshotError("forecast_split_membership_outside_feature_set")
                row = InputRow.model_validate_json(found[0])
                fold = folds[membership.fold]
                label_raw = db.execute(
                    "SELECT body FROM rows WHERE kind='labels' AND key=?", (key(membership),)
                ).fetchone()
                label = LabelPoint.model_validate_json(label_raw[0]) if label_raw else None
                if label is not None:
                    label_count += 1
                    if label.knowledge_cutoff != fold.label_cutoff(membership.role) or any(
                        getattr(label, k) != getattr(row, k) for k in FORECAST_FIELDS
                    ):
                        raise SnapshotError("forecast_split_label_cutoff_or_key_mismatch")
                if membership != qualify(
                    row,
                    get_history(row.history_context_sha256),
                    fold,
                    features.descriptor.resolved_policy,
                    label,
                ):
                    raise SnapshotError("forecast_split_membership_qualification_mismatch")
                count_membership(counts, membership)
            if (
                dict(counts) != manifest.descriptor.counts
                or label_count != manifest.labels.row_count
                or manifest.descriptor.row_count != features.descriptor.row_count * len(folds)
            ):
                raise SnapshotError("forecast_split_coverage_mismatch")
        except sqlite3.IntegrityError as exc:
            raise SnapshotError("duplicate_forecast_split_record") from exc
        finally:
            db.close()
    inventory(root, names)
    return manifest
