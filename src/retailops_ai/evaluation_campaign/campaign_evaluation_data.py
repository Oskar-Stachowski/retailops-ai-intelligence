"""Internal complete-role indexing after reservation; metadata is not access permission.

Inference and actuals use separate private databases. Only the preparation step
reads the declared outcome file, once. The public runner must prove completion
of parents and frozen selection before invoking that step.
"""

import hashlib
import json
import sqlite3
import zlib
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, get_args

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
    EvaluationRole,
)
from retailops_ai.evaluation_campaign.campaign_final_contract import (
    FinalForecastExample,
    FinalForecastManifest,
    FinalForecastOutcome,
)
from retailops_ai.evaluation_campaign.campaign_forecast_inference import (
    InferenceRecord,
    InferenceWindow,
)
from retailops_ai.evaluation_campaign.campaign_forecast_inputs import _horizons
from retailops_ai.evaluation_campaign.label_contract import (
    EligibilityReason,
    QualifiedForecastOutcome,
)
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.evaluation_campaign.physical_contract import (
    PhysicalForecastExample,
    PhysicalForecastManifest,
    PhysicalRoleFile,
)
from retailops_ai.evaluation_campaign.physical_forecast import _role_file
from retailops_ai.evaluation_campaign.physical_versions import check_index
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.manifests import input_models
from retailops_ai.source_snapshot.files import SnapshotError, regular_file

REASONS = frozenset(get_args(EligibilityReason))
Manifest = PhysicalForecastManifest | FinalForecastManifest


@dataclass(frozen=True)
class EvaluationRecord:
    key: bytes
    row: InputRow
    role: EvaluationRole
    example_sha256: str
    eligible: bool
    exclusion_reasons: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationWindow:
    records: tuple[EvaluationRecord, ...]
    history: HistoryContext

    def inference(self) -> InferenceWindow:
        return InferenceWindow(
            tuple(InferenceRecord(r.key, r.row, r.eligible) for r in self.records), self.history
        )


@dataclass(frozen=True)
class EvaluationActual:
    key: bytes
    role: EvaluationRole
    example_sha256: str
    eligible: bool
    actual: int | None


def _plan(plan: CampaignForecastEvaluationPlan) -> CampaignForecastEvaluationPlan:
    return CampaignForecastEvaluationPlan.model_validate_json(
        canonical_bytes(plan.model_dump(mode="json"))
    )


def _scope(
    manifest: Manifest, plan: CampaignForecastEvaluationPlan
) -> tuple[str, int, PhysicalRoleFile]:
    manifest = type(manifest).model_validate_json(canonical_bytes(manifest.model_dump(mode="json")))
    if isinstance(manifest, PhysicalForecastManifest) and plan.role == "development_evaluation":
        recipe = manifest.descriptor.recipe
        return (
            "development_evaluation.jsonl",
            recipe.max_record_bytes,
            manifest.descriptor.populations[plan.role],
        )
    if isinstance(manifest, FinalForecastManifest) and plan.role == "final_test":
        final = manifest.descriptor.recipe.plan
        if final.source_recipe_sha256 != plan.source_recipe_sha256:
            raise SnapshotError("campaign_evaluation_final_source_recipe_mismatch")
        return "final_evaluation.jsonl", final.max_record_bytes, manifest.descriptor.population
    raise SnapshotError("campaign_evaluation_manifest_phase_role_mismatch")


def _check_indexes(db: sqlite3.Connection, outcomes: sqlite3.Connection, maximum: int) -> None:
    check_index(db, maximum)
    check_index(outcomes, maximum)
    used = sum(
        connection.execute(
            "SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()"
        ).fetchone()[0]
        for connection in (db, outcomes)
    )
    if used > maximum:
        raise SnapshotError("campaign_evaluation_combined_index_budget")


def _separate_indexes(db: sqlite3.Connection, outcomes: sqlite3.Connection) -> None:
    paths = []
    for connection in (db, outcomes):
        databases = connection.execute("PRAGMA database_list").fetchall()
        if any(name not in {"main", "temp"} for _, name, _ in databases):
            raise SnapshotError("campaign_evaluation_separate_actual_index_required")
        paths.append(next(path for _, name, path in databases if name == "main"))
    if db is outcomes or (all(paths) and Path(paths[0]).samefile(paths[1])):
        raise SnapshotError("campaign_evaluation_separate_actual_index_required")


def index_role(
    db: sqlite3.Connection,
    outcomes: sqlite3.Connection,
    dataset: Path,
    manifest: Manifest,
    plan: CampaignForecastEvaluationPlan,
) -> dict[str, Any]:
    """No permission grant: call only inside the supervised, reserved prepare worker."""
    plan = _plan(plan)
    name, maximum_record, expected = _scope(manifest, plan)
    _separate_indexes(db, outcomes)
    db.execute(
        "CREATE TABLE examples(key BLOB PRIMARY KEY,example_sha256 TEXT,feature_sha256 TEXT,eligible INTEGER,reasons BLOB,feature BLOB,history TEXT,group_key BLOB,horizon INTEGER)"
    )
    db.execute("CREATE INDEX examples_group ON examples(group_key,horizon)")
    db.execute("CREATE INDEX examples_history ON examples(history)")
    db.execute("CREATE TABLE histories(hash TEXT PRIMARY KEY,body BLOB)")
    outcomes.execute(
        "CREATE TABLE actuals(key BLOB PRIMARY KEY,example_sha256 TEXT,eligible INTEGER,actual INTEGER)"
    )
    _check_indexes(db, outcomes, plan.max_index_bytes)
    counter: Counter[str] = Counter()
    content, keys, eligible_keys = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    size = 0
    previous = None
    with regular_file(dataset, name) as stream:
        while line := stream.readline(maximum_record + 1):
            if len(line) > maximum_record or not line.endswith(b"\n"):
                raise SnapshotError("campaign_evaluation_role_record_limit")
            outcome: QualifiedForecastOutcome | FinalForecastOutcome
            if plan.role == "final_test":
                final_example = FinalForecastExample.model_validate_json(line)
                key = membership_key(final_example.key)
                outcome = final_example.outcome
                raw = canonical_bytes(final_example.model_dump(mode="json"))
            else:
                example = PhysicalForecastExample.model_validate_json(line)
                if example.membership.role != plan.role or example.outcome is None:
                    raise SnapshotError("campaign_evaluation_wrong_role_or_missing_outcome")
                key = membership_key(example.membership)
                outcome = example.outcome
                raw = canonical_bytes(example.model_dump(mode="json"))
            if (previous is not None and key <= previous) or line != raw + b"\n":
                raise SnapshotError("campaign_evaluation_noncanonical_or_unsorted_role")
            previous = key
            counter["rows"] += 1
            if counter["rows"] > plan.max_rows:
                raise SnapshotError("campaign_evaluation_complete_population_limit")
            counter["eligible"] += int(outcome.eligible)
            counter["censored"] += int(outcome.label.status == "censored")
            counter["zero"] += int(outcome.label.observed_sales_units == 0)
            counter.update("reason:" + r for r in outcome.reasons)
            size += len(line)
            content.update(line)
            keys.update(key + b"\n")
            if outcome.eligible:
                eligible_keys.update(key + b"\n")
            digest = hashlib.sha256(raw).hexdigest()
            db.execute(
                "INSERT INTO examples(key,example_sha256,feature_sha256,eligible,reasons) VALUES(?,?,?,?,?)",
                (
                    key,
                    digest,
                    outcome.feature_row_sha256,
                    int(outcome.eligible),
                    canonical_bytes(outcome.reasons),
                ),
            )
            outcomes.execute(
                "INSERT INTO actuals VALUES(?,?,?,?)",
                (
                    key,
                    digest,
                    int(outcome.eligible),
                    outcome.label.observed_sales_units if outcome.eligible else None,
                ),
            )
            if counter["rows"] % 256 == 0:
                _check_indexes(db, outcomes, plan.max_index_bytes)
    observed = _role_file(counter, size, content.hexdigest(), keys.hexdigest())
    if not counter["rows"] or observed != expected:
        raise SnapshotError("campaign_evaluation_complete_role_receipt_mismatch")
    for index, row in enumerate(input_models(dataset / "features", "features"), 1):
        if not isinstance(row, InputRow):
            raise SnapshotError("campaign_evaluation_feature_schema")
        key = membership_key(row)
        found = db.execute(
            "SELECT feature_sha256,feature FROM examples WHERE key=?", (key,)
        ).fetchone()
        if found is None and plan.role == "final_test":
            raise SnapshotError("campaign_evaluation_final_unexpected_feature_key")
        if found is not None:
            raw = canonical_bytes(row.model_dump(mode="json"))
            if found[1] is not None or found[0] != hashlib.sha256(raw).hexdigest():
                raise SnapshotError("campaign_evaluation_feature_role_hash_mismatch")
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
                (zlib.compress(raw, 1), row.history_context_sha256, group, row.horizon_days, key),
            )
        if index % 256 == 0:
            _check_indexes(db, outcomes, plan.max_index_bytes)
    if db.execute("SELECT 1 FROM examples WHERE feature IS NULL LIMIT 1").fetchone():
        raise SnapshotError("campaign_evaluation_missing_feature_key")
    for index, history in enumerate(input_models(dataset / "features", "history"), 1):
        if not isinstance(history, HistoryContext):
            raise SnapshotError("campaign_evaluation_history_schema")
        digest = history.content_sha256()
        if db.execute("SELECT 1 FROM examples WHERE history=? LIMIT 1", (digest,)).fetchone():
            if db.execute("SELECT 1 FROM histories WHERE hash=?", (digest,)).fetchone():
                raise SnapshotError("campaign_evaluation_duplicate_history")
            db.execute(
                "INSERT INTO histories VALUES(?,?)",
                (digest, zlib.compress(canonical_bytes(history.model_dump(mode="json")), 1)),
            )
        if index % 256 == 0:
            _check_indexes(db, outcomes, plan.max_index_bytes)
    if db.execute(
        "SELECT 1 FROM examples e LEFT JOIN histories h ON e.history=h.hash WHERE h.hash IS NULL LIMIT 1"
    ).fetchone():
        raise SnapshotError("campaign_evaluation_missing_history")
    result = {
        "role": plan.role,
        "dataset_id": manifest.dataset_id,
        "plan_sha256": plan.content_sha256(),
        "rows": counter["rows"],
        "eligible_rows": counter["eligible"],
        "keys_sha256": keys.hexdigest(),
        "eligible_keys_sha256": eligible_keys.hexdigest(),
        "role_population_sha256": content.hexdigest(),
    }
    for connection in (db, outcomes):
        connection.execute("CREATE TABLE evaluation_metadata(body BLOB)")
        connection.execute("INSERT INTO evaluation_metadata VALUES(?)", (canonical_bytes(result),))
    _check_indexes(db, outcomes, plan.max_index_bytes)
    # Every window is checked, including windows excluded from every model.
    for _ in windows(db, plan):
        pass
    return result


def _metadata(db: sqlite3.Connection, plan: CampaignForecastEvaluationPlan) -> dict[str, Any]:
    if any(name not in {"main", "temp"} for _, name, _ in db.execute("PRAGMA database_list")):
        raise SnapshotError("campaign_evaluation_separate_actual_index_required")
    records = db.execute("SELECT body FROM evaluation_metadata LIMIT 2").fetchall()
    if len(records) != 1:
        raise SnapshotError("campaign_evaluation_index_scope_missing")
    value = json.loads(records[0][0])
    if (
        not isinstance(value, dict)
        or value.get("role") != plan.role
        or value.get("plan_sha256") != plan.content_sha256()
    ):
        raise SnapshotError("campaign_evaluation_index_scope_mismatch")
    return value


def windows(
    db: sqlite3.Connection, plan: CampaignForecastEvaluationPlan
) -> Iterator[EvaluationWindow]:
    plan = _plan(plan)
    expected = _metadata(db, plan)
    count = 0
    for (group,) in db.execute("SELECT DISTINCT group_key FROM examples ORDER BY group_key"):
        records = db.execute(
            "SELECT key,feature,history,example_sha256,feature_sha256,eligible,reasons FROM examples WHERE group_key=? ORDER BY horizon LIMIT 15",
            (group,),
        ).fetchall()
        hashes = {record[2] for record in records}
        if not 1 <= len(records) <= 14 or len(hashes) != 1:
            raise SnapshotError("campaign_evaluation_window_population_or_history_mismatch")
        found = db.execute("SELECT body FROM histories WHERE hash=?", (hashes.pop(),)).fetchone()
        if found is None:
            raise SnapshotError("campaign_evaluation_missing_history")
        history = HistoryContext.model_validate_json(zlib.decompress(found[0]))
        decoded = []
        for key, feature, _, example_sha, feature_sha, eligible, reasons_raw in records:
            raw = zlib.decompress(feature)
            row = InputRow.model_validate_json(raw)
            reasons = json.loads(reasons_raw)
            if (
                membership_key(row) != key
                or hashlib.sha256(raw).hexdigest() != feature_sha
                or eligible not in (0, 1)
                or not isinstance(reasons, list)
                or any(not isinstance(r, str) or r not in REASONS for r in reasons)
                or len(set(reasons)) != len(reasons)
                or bool(eligible) != (not reasons)
            ):
                raise SnapshotError("campaign_evaluation_inference_metadata_mismatch")
            decoded.append(
                EvaluationRecord(key, row, plan.role, example_sha, bool(eligible), tuple(reasons))
            )
        _horizons((record.row for record in decoded), history)
        count += len(decoded)
        if count > plan.max_rows:
            raise SnapshotError("campaign_evaluation_complete_population_limit")
        yield EvaluationWindow(tuple(decoded), history)
    if count != expected["rows"]:
        raise SnapshotError("campaign_evaluation_inference_population_mismatch")


def batches(
    db: sqlite3.Connection, plan: CampaignForecastEvaluationPlan
) -> Iterator[list[EvaluationWindow]]:
    plan = _plan(plan)
    iterator = windows(db, plan)
    while batch := list(islice(iterator, plan.batch_windows)):
        yield batch


def actuals(
    outcomes: sqlite3.Connection, plan: CampaignForecastEvaluationPlan
) -> Iterator[EvaluationActual]:
    """Aggregation only; never hand this connection or its path to an inference worker."""
    plan = _plan(plan)
    expected = _metadata(outcomes, plan)
    count = 0
    for key, digest, eligible, actual in outcomes.execute(
        "SELECT key,example_sha256,eligible,actual FROM actuals ORDER BY key"
    ):
        if eligible not in (0, 1) or (
            type(actual) is not int or actual < 0 if eligible else actual is not None
        ):
            raise SnapshotError("campaign_evaluation_actual_eligibility_mismatch")
        count += 1
        if count > plan.max_rows:
            raise SnapshotError("campaign_evaluation_complete_population_limit")
        yield EvaluationActual(key, plan.role, digest, bool(eligible), actual)
    if count != expected["rows"]:
        raise SnapshotError("campaign_evaluation_actual_population_mismatch")
