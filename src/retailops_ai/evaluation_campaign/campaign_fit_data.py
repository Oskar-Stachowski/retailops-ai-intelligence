"""Disk-indexed complete role data and train-only learned encoding, without sampling."""

import hashlib
import math
import os
import sqlite3
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

import numpy as np

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_fit_contract import (
    CampaignCategoricalEncoding,
    CampaignForecastEncoding,
    CampaignForecastFitPlan,
    CampaignNumericEncoding,
)
from retailops_ai.evaluation_campaign.campaign_forecast_inputs import (
    tensorflow_vector,
    tree_vector,
)
from retailops_ai.evaluation_campaign.campaign_forecast_inputs import (
    transform as transform,
)
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.evaluation_campaign.physical_contract import (
    PhysicalForecastExample,
    PhysicalForecastManifest,
)
from retailops_ai.evaluation_campaign.physical_versions import check_index
from retailops_ai.forecasting.features_contract import FEATURE_TYPES, HistoryContext, InputRow
from retailops_ai.forecasting.manifests import input_models
from retailops_ai.source_snapshot.files import SnapshotError, checked_directory, regular_file

ROLES: tuple[Literal["train", "early_stopping"], ...] = ("train", "early_stopping")


def _memmap(output: Path, name: str, dtype: Any, shape: tuple[int, ...]) -> Any:
    checked_directory(output)
    descriptor = os.open(output / name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    return np.lib.format.open_memmap(output / name, mode="w+", dtype=dtype, shape=shape)


def _label(example: PhysicalForecastExample) -> int:
    if example.outcome is None or example.outcome.label.observed_sales_units is None:
        raise SnapshotError("campaign_fit_eligible_label_missing")
    return example.outcome.label.observed_sales_units


def index_roles(
    db: sqlite3.Connection,
    dataset: Path,
    manifest: PhysicalForecastManifest,
    plan: CampaignForecastFitPlan,
) -> dict[str, int]:
    db.execute(
        "CREATE TABLE examples(key BLOB PRIMARY KEY,role TEXT,eligible INTEGER,target REAL,history TEXT,body BLOB,feature BLOB,group_key BLOB,horizon INTEGER)"
    )
    db.execute("CREATE INDEX examples_role ON examples(role,key)")
    db.execute("CREATE INDEX examples_group ON examples(role,group_key,horizon)")
    db.execute("CREATE INDEX examples_history ON examples(history)")
    counts: dict[str, int] = {role: 0 for role in ROLES}
    for role in ROLES:
        digest = hashlib.sha256()
        size = 0
        previous = None
        with regular_file(dataset, role + ".jsonl") as stream:
            while line := stream.readline(manifest.descriptor.recipe.max_record_bytes + 1):
                if len(line) > manifest.descriptor.recipe.max_record_bytes or not line.endswith(
                    b"\n"
                ):
                    raise SnapshotError("campaign_fit_role_record_limit")
                example = PhysicalForecastExample.model_validate_json(line)
                key = membership_key(example.membership)
                if (
                    example.membership.role != role
                    or example.outcome is None
                    or (previous is not None and key <= previous)
                    or line != canonical_bytes(example.model_dump(mode="json")) + b"\n"
                ):
                    raise SnapshotError("campaign_fit_noncanonical_or_wrong_role")
                previous = key
                counts[role] += 1
                size += len(line)
                digest.update(line)
                outcome = example.outcome
                db.execute(
                    "INSERT INTO examples(key,role,eligible,target,body) VALUES(?,?,?,?,?)",
                    (
                        key,
                        role,
                        int(outcome.eligible),
                        outcome.label.observed_sales_units,
                        zlib.compress(line, 1),
                    ),
                )
                if counts[role] > plan.max_train_rows * 14:
                    raise SnapshotError("campaign_fit_role_population_limit")
                if counts[role] % 256 == 0:
                    check_index(db, plan.max_index_bytes)
        expected = manifest.descriptor.populations[role]
        if (counts[role], size, digest.hexdigest()) != (
            expected.row_count,
            expected.size_bytes,
            expected.sha256,
        ):
            raise SnapshotError("campaign_fit_complete_role_receipt_mismatch")
        eligible = db.execute(
            "SELECT count(*) FROM examples WHERE role=? AND eligible=1", (role,)
        ).fetchone()[0]
        if not 1 <= eligible <= plan.max_train_rows:
            raise SnapshotError("campaign_fit_eligible_population_limit")
    for count, row in enumerate(input_models(dataset / "features", "features"), 1):
        if not isinstance(row, InputRow):
            raise SnapshotError("campaign_fit_feature_schema")
        key = membership_key(row)
        found = db.execute("SELECT body FROM examples WHERE key=?", (key,)).fetchone()
        if found is not None:
            example = PhysicalForecastExample.model_validate_json(zlib.decompress(found[0]))
            if (
                example.outcome is None
                or example.outcome.feature_row_sha256
                != hashlib.sha256(canonical_bytes(row.model_dump(mode="json"))).hexdigest()
            ):
                raise SnapshotError("campaign_fit_feature_role_hash_mismatch")
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
        if count % 256 == 0:
            check_index(db, plan.max_index_bytes)
    if db.execute("SELECT 1 FROM examples WHERE feature IS NULL LIMIT 1").fetchone():
        raise SnapshotError("campaign_fit_missing_feature_key")
    db.execute("CREATE TABLE histories(hash TEXT PRIMARY KEY,body BLOB)")
    for count, history in enumerate(input_models(dataset / "features", "history"), 1):
        if not isinstance(history, HistoryContext):
            raise SnapshotError("campaign_fit_history_schema")
        identifier = history.content_sha256()
        if db.execute("SELECT 1 FROM examples WHERE history=? LIMIT 1", (identifier,)).fetchone():
            db.execute(
                "INSERT INTO histories VALUES(?,?)",
                (identifier, zlib.compress(canonical_bytes(history.model_dump(mode="json")), 1)),
            )
        if count % 256 == 0:
            check_index(db, plan.max_index_bytes)
    if db.execute(
        "SELECT 1 FROM examples e LEFT JOIN histories h ON e.history=h.hash WHERE h.hash IS NULL LIMIT 1"
    ).fetchone():
        raise SnapshotError("campaign_fit_missing_history")
    for role in ROLES:
        windows = db.execute(
            "SELECT count(DISTINCT group_key) FROM examples WHERE role=? AND group_key IN (SELECT group_key FROM examples WHERE role=? AND eligible=1)",
            (role, role),
        ).fetchone()[0]
        if windows > plan.max_windows:
            raise SnapshotError("campaign_fit_window_budget")
    check_index(db, plan.max_index_bytes)
    return counts


def rows(
    db: sqlite3.Connection, role: str, *, eligible: bool = True
) -> Iterator[tuple[bytes, InputRow, PhysicalForecastExample]]:
    if role not in ROLES:
        raise SnapshotError("campaign_fit_training_or_early_stopping_only")
    for key, feature, example in db.execute(
        "SELECT key,feature,body FROM examples WHERE role=? AND (?=0 OR eligible=1) ORDER BY key",
        (role, int(eligible)),
    ):
        yield (
            key,
            InputRow.model_validate_json(zlib.decompress(feature)),
            PhysicalForecastExample.model_validate_json(zlib.decompress(example)),
        )


def _median(db: sqlite3.Connection, table: str, *, name: str | None = None) -> tuple[float, int]:
    if table not in {"numeric_values", "history_values"}:
        raise SnapshotError("campaign_fit_unallowlisted_statistics_table")
    if table == "numeric_values" and name is not None:
        count = db.execute("SELECT count(*) FROM numeric_values WHERE name=?", (name,)).fetchone()[
            0
        ]
    elif table == "history_values" and name is None:
        count = db.execute("SELECT count(*) FROM history_values").fetchone()[0]
    else:
        raise SnapshotError("campaign_fit_statistics_scope_mismatch")
    if not count:
        return 0.0, 0
    limits = (2 if count % 2 == 0 else 1, (count - 1) // 2)
    if table == "numeric_values":
        values = db.execute(
            "SELECT value FROM numeric_values WHERE name=? ORDER BY value LIMIT ? OFFSET ?",
            (name, *limits),
        ).fetchall()
    else:
        values = db.execute(
            "SELECT value FROM history_values ORDER BY value LIMIT ? OFFSET ?", limits
        ).fetchall()
    return math.fsum(v[0] for v in values) / len(values), count


def fit_encoding(db: sqlite3.Connection, plan: CampaignForecastFitPlan) -> CampaignForecastEncoding:
    db.execute("CREATE TABLE numeric_values(name TEXT,value REAL)")
    db.execute("CREATE INDEX numeric_order ON numeric_values(name,value)")
    db.execute("CREATE TABLE categories(name TEXT,value TEXT,PRIMARY KEY(name,value))")
    db.execute("CREATE TABLE history_values(value REAL)")
    db.execute("CREATE INDEX history_order ON history_values(value)")
    keys, labels = hashlib.sha256(), hashlib.sha256()
    count = 0
    for key, row, example in rows(db, "train"):
        count += 1
        keys.update(key + b"\n")
        if example.outcome is None:
            raise SnapshotError("campaign_fit_train_outcome_missing")
        labels.update(canonical_bytes(example.outcome.label.model_dump(mode="json")) + b"\n")
        for item in row.values:
            if item.value is None:
                continue
            if FEATURE_TYPES[item.name] == "str":
                db.execute("INSERT OR IGNORE INTO categories VALUES(?,?)", (item.name, item.value))
            else:
                number = float(item.value)
                if not math.isfinite(number):
                    raise SnapshotError("campaign_fit_nonfinite_train_value")
                db.execute("INSERT INTO numeric_values VALUES(?,?)", (item.name, number))
        if count % 256 == 0:
            check_index(db, plan.max_index_bytes)
    if not 1 <= count <= plan.max_train_rows:
        raise SnapshotError("campaign_fit_train_population_limit")
    numeric: list[CampaignNumericEncoding] = []
    categorical: list[CampaignCategoricalEncoding] = []
    columns: list[str] = []
    for name, kind in FEATURE_TYPES.items():
        if kind == "str":
            categories = tuple(
                r[0]
                for r in db.execute(
                    "SELECT value FROM categories WHERE name=? ORDER BY value LIMIT 1001", (name,)
                )
            )
            if len(categories) > 1000:
                raise SnapshotError("campaign_fit_vocabulary_limit")
            categorical.append(CampaignCategoricalEncoding(name=name, categories=categories))
        else:
            fill, known = _median(db, "numeric_values", name=name)
            if kind == "bool" and known:
                fill = float(
                    db.execute(
                        "SELECT sum(value) FROM numeric_values WHERE name=?", (name,)
                    ).fetchone()[0]
                    > known / 2
                )
            total = (
                db.execute(
                    "SELECT coalesce(sum(value),0) FROM numeric_values WHERE name=?", (name,)
                ).fetchone()[0]
                + (count - known) * fill
            )
            center = total / count
            moment = (
                db.execute(
                    "SELECT coalesce(sum((value-?)*(value-?)),0) FROM numeric_values WHERE name=?",
                    (center, center, name),
                ).fetchone()[0]
                + (count - known) * (fill - center) ** 2
            )
            numeric.append(
                CampaignNumericEncoding(
                    name=name,
                    fill=fill,
                    known_count=known,
                    center=center,
                    spread=math.sqrt(max(0.0, moment) / count) or 1.0,
                )
            )
            columns.extend((name, name + "__missing"))
    for category in categorical:
        columns.extend((category.name + "__missing", category.name + "__unknown"))
        columns.extend(category.name + f"__category_{i}" for i in range(len(category.categories)))
    for (body,) in db.execute(
        "SELECT h.body FROM histories h WHERE EXISTS (SELECT 1 FROM examples e WHERE e.history=h.hash AND e.role='train' AND e.eligible=1) ORDER BY h.hash"
    ):
        history = HistoryContext.model_validate_json(zlib.decompress(body))
        for point in history.points:
            if point.observed_units is not None:
                db.execute("INSERT INTO history_values VALUES(?)", (float(point.observed_units),))
    fill, history_count = _median(db, "history_values")
    center = (
        db.execute("SELECT avg(value) FROM history_values").fetchone()[0] if history_count else 0.0
    )
    moment = (
        db.execute(
            "SELECT sum((value-?)*(value-?)) FROM history_values", (center, center)
        ).fetchone()[0]
        if history_count
        else 0.0
    )
    target = db.execute(
        "SELECT avg(target) FROM examples WHERE role='train' AND eligible=1"
    ).fetchone()[0]
    check_index(db, plan.max_index_bytes)
    return CampaignForecastEncoding(
        train_keys_sha256=keys.hexdigest(),
        train_labels_sha256=labels.hexdigest(),
        train_rows=count,
        numeric=tuple(numeric),
        categorical=tuple(categorical),
        history_fill=fill,
        history_center=float(center),
        history_spread=math.sqrt(max(0.0, moment) / history_count)
        if history_count and moment
        else 1.0,
        target_scale=max(1.0, target),
        output_columns=tuple(columns),
    )


def tree_matrices(
    db: sqlite3.Connection,
    role: str,
    encoding: CampaignForecastEncoding,
    plan: CampaignForecastFitPlan,
    output: Path,
) -> dict[str, Any]:
    if role not in ROLES:
        raise SnapshotError("campaign_fit_training_or_early_stopping_only")
    count = db.execute(
        "SELECT count(*) FROM examples WHERE role=? AND eligible=1", (role,)
    ).fetchone()[0]
    width = len(encoding.output_columns) + 1
    if not 1 <= count <= plan.max_train_rows or count * (width + 1) * 8 > plan.max_matrix_bytes:
        raise SnapshotError("campaign_fit_matrix_budget")
    x = _memmap(output, role + "-x.npy", np.float64, (count, width))
    y = _memmap(output, role + "-y.npy", np.float64, (count,))
    keys = hashlib.sha256()
    for index, (key, row, example) in enumerate(rows(db, role)):
        x[index] = tree_vector(row, encoding)
        y[index] = _label(example)
        keys.update(key + b"\n")
    x.flush()
    y.flush()
    return {"rows": count, "columns": width, "keys_sha256": keys.hexdigest()}


def tensorflow_matrices(
    db: sqlite3.Connection,
    role: str,
    encoding: CampaignForecastEncoding,
    plan: CampaignForecastFitPlan,
    output: Path,
) -> dict[str, Any]:
    if role not in ROLES:
        raise SnapshotError("campaign_fit_training_or_early_stopping_only")
    groups = [
        r[0]
        for r in db.execute(
            "SELECT DISTINCT group_key FROM examples WHERE role=? AND eligible=1 ORDER BY group_key",
            (role,),
        )
    ]
    count = len(groups)
    width = encoding.tensorflow_width
    if not 1 <= count <= plan.max_windows or count * (width + 28) * 4 > plan.max_matrix_bytes:
        raise SnapshotError("campaign_fit_tensorflow_matrix_budget")
    x = _memmap(output, role + "-x.npy", np.float32, (count, width))
    y = _memmap(output, role + "-y.npy", np.float32, (count, 14))
    mask = _memmap(output, role + "-mask.npy", np.float32, (count, 14))
    eligible = 0
    for index, group in enumerate(groups):
        records = db.execute(
            "SELECT feature,body,history FROM examples WHERE role=? AND group_key=? ORDER BY horizon",
            (role, group),
        ).fetchall()
        histories = {r[2] for r in records}
        if len(histories) != 1:
            raise SnapshotError("campaign_fit_window_history_mismatch")
        history = HistoryContext.model_validate_json(
            zlib.decompress(
                db.execute(
                    "SELECT body FROM histories WHERE hash=?", (histories.pop(),)
                ).fetchone()[0]
            )
        )
        horizons = {}
        for feature, example, _ in records:
            row = InputRow.model_validate_json(zlib.decompress(feature))
            value = PhysicalForecastExample.model_validate_json(zlib.decompress(example))
            if row.horizon_days in horizons:
                raise SnapshotError("campaign_fit_duplicate_horizon")
            horizons[row.horizon_days] = (row, value)
        y[index] = 0
        mask[index] = 0
        x[index] = tensorflow_vector((row for row, _ in horizons.values()), history, encoding)
        for horizon, (_, example) in horizons.items():
            if example.outcome is None:
                raise SnapshotError("campaign_fit_window_outcome_missing")
            if example.outcome.eligible:
                y[index, horizon - 1] = _label(example) / encoding.target_scale
                mask[index, horizon - 1] = 1
                eligible += 1
    expected = db.execute(
        "SELECT count(*) FROM examples WHERE role=? AND eligible=1", (role,)
    ).fetchone()[0]
    if eligible != expected:
        raise SnapshotError("campaign_fit_tensorflow_dropped_eligible_key")
    keys = hashlib.sha256()
    for (key,) in db.execute(
        "SELECT key FROM examples WHERE role=? AND eligible=1 ORDER BY key", (role,)
    ):
        keys.update(key + b"\n")
    x.flush()
    y.flush()
    mask.flush()
    return {
        "windows": count,
        "eligible_rows": eligible,
        "columns": width,
        "keys_sha256": keys.hexdigest(),
    }
