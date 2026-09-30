"""Immutable dual-target campaign; selection precedes every holdout score, replay never refits."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import zlib
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.functional_contract import HEADS, FunctionalPipeline, FunctionalPolicy
from retailops_ai.forecasting.functional_models import fit_worker, functional_code
from retailops_ai.forecasting.functional_preprocessing import (
    fit_ordered_train_samples,
    transform_values,
)
from retailops_ai.forecasting.functional_recipe import (
    Observation,
    empirical_baselines,
    fit_recipe,
    predict_pair,
)
from retailops_ai.forecasting.manifest_contract import (
    FeatureManifest,
    FoldPlan,
    LabelPoint,
    Membership,
    Role,
    SplitManifest,
)
from retailops_ai.forecasting.manifest_io import iter_table, key
from retailops_ai.forecasting.manifests import feature_key, input_models, load_feature_set
from retailops_ai.forecasting.model_contract import MAX_MODEL_BYTES
from retailops_ai.forecasting.model_trees import TreePredictor
from retailops_ai.forecasting.models import decode_model_json, model_code
from retailops_ai.forecasting.quality_contract import QualityPolicy
from retailops_ai.forecasting.quality_metrics import volume_bin
from retailops_ai.forecasting.quality_v2 import assess_segment_v2
from retailops_ai.forecasting.quality_v2_contract import ProtocolObservation
from retailops_ai.forecasting.splits import verify_split
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    inventory,
    read_bytes,
    read_json,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

ROLES = ("validation", "development_holdout")


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_bytes(value) + b"\n")


def index_parents(
    db: sqlite3.Connection, features: Path, split_dir: Path, policy: FunctionalPolicy
) -> tuple[FeatureManifest, SplitManifest, dict[str, list[str]]]:
    split = verify_split(split_dir, features)
    feature = load_feature_set(features)
    if split.descriptor.qualification_status != "passed":
        raise SnapshotError("functional_split_not_ready")
    page = db.execute("PRAGMA page_size").fetchone()[0]
    db.execute(f"PRAGMA max_page_count={policy.max_index_bytes // page}")
    db.execute("PRAGMA cache_size=-4096")
    db.execute("PRAGMA temp_store=FILE")
    db.execute("CREATE TABLE features (key BLOB PRIMARY KEY,body BLOB)")
    db.execute("CREATE TABLE history (key TEXT PRIMARY KEY,body BLOB)")
    db.execute(
        "CREATE TABLE members (key TEXT PRIMARY KEY,fkey BLOB,fold TEXT,role TEXT,eligible INTEGER,body BLOB)"
    )
    db.execute("CREATE INDEX member_roles ON members(fold,role,eligible,fkey)")
    db.execute("CREATE TABLE labels (key TEXT PRIMARY KEY,body BLOB)")
    db.execute(
        "CREATE TABLE observations (key TEXT PRIMARY KEY,fold TEXT,role TEXT,volume TEXT,category TEXT,channel TEXT,horizon TEXT,body BLOB)"
    )
    dimensions: dict[str, set[str]] = {"category": set(), "channel": set(), "volume": set()}
    for row in input_models(features, "history"):
        if not isinstance(row, HistoryContext):
            raise SnapshotError("functional_history_schema")
        db.execute(
            "INSERT INTO history VALUES (?,?)",
            (row.content_sha256(), zlib.compress(canonical_bytes(row.model_dump(mode="json")))),
        )
    for row in input_models(features, "features"):
        if not isinstance(row, InputRow):
            raise SnapshotError("functional_feature_schema")
        values = {v.name: v.value for v in row.values}
        dimensions["category"].add(str(values["category_id"]))
        dimensions["channel"].add(row.channel)
        dimensions["volume"].add(
            volume_bin(cast(float | None, values["rolling_mean_28"]), QualityPolicy())
        )
        db.execute(
            "INSERT INTO features VALUES (?,?)",
            (feature_key(row), zlib.compress(canonical_bytes(row.model_dump(mode="json")))),
        )
    budget: Counter[str] = Counter()
    for kind in ("memberships", "labels"):
        for record in iter_table(split_dir, kind, split.tables[kind], budget):
            if record.role == "purged":
                continue
            body = zlib.compress(canonical_bytes(record.model_dump(mode="json")))
            if isinstance(record, Membership):
                db.execute(
                    "INSERT INTO members VALUES (?,?,?,?,?,?)",
                    (
                        key(record).decode(),
                        feature_key(record),
                        record.fold,
                        record.role,
                        record.eligible,
                        body,
                    ),
                )
            else:
                db.execute("INSERT INTO labels VALUES (?,?)", (key(record).decode(), body))
    missing = db.execute(
        "SELECT COUNT(*) FROM members m LEFT JOIN features f ON f.key=m.fkey LEFT JOIN labels l ON l.key=m.key WHERE f.key IS NULL OR l.key IS NULL"
    ).fetchone()[0]
    if missing:
        raise SnapshotError("functional_parent_keys_incomplete")
    dimensions["volume"].update(policy.quality.required_volume_bins)
    db.commit()
    return feature, split, {name: sorted(values) for name, values in dimensions.items()}


def samples(
    db: sqlite3.Connection, fold: FoldPlan, role: str, eligible_only: bool = False
) -> Iterator[tuple[InputRow, Membership, LabelPoint]]:
    for raw_row, raw_member, raw_label in db.execute(
        "SELECT f.body,m.body,l.body FROM members m JOIN features f ON f.key=m.fkey JOIN labels l ON l.key=m.key WHERE m.fold=? AND m.role=? AND (?=0 OR m.eligible=1) ORDER BY m.fkey",
        (fold.name, role, int(eligible_only)),
    ):
        row = InputRow.model_validate_json(zlib.decompress(raw_row))
        member = Membership.model_validate_json(zlib.decompress(raw_member))
        label = LabelPoint.model_validate_json(zlib.decompress(raw_label))
        if member.eligible and (
            label.status != "eligible"
            or label.observed_sales_units is None
            or label.label_available_at is None
            or label.label_available_at > fold.label_cutoff(cast(Role, role))
        ):
            raise SnapshotError("functional_requires_mature_role_labels")
        yield row, member, label


def fit_fold(
    db: sqlite3.Connection,
    feature: FeatureManifest,
    split: SplitManifest,
    fold: FoldPlan,
    policy: FunctionalPolicy,
    root: Path,
    replay: Path | None,
) -> dict[str, FunctionalPipeline]:
    state = fit_ordered_train_samples(
        ((r, m) for r, m, _ in samples(db, fold, "train", True)),
        fold=fold,
        policy=feature.descriptor.resolved_policy,
        feature_set_id=feature.feature_set_id,
        split_id=split.split_id,
    )
    n = state.descriptor.train_rows
    columns = len(state.descriptor.output_columns) + 1
    if n > policy.model.max_train_rows or n * (columns + 1) * 8 > policy.model.max_matrix_bytes:
        raise SnapshotError("forecast_model_training_matrix_budget")
    x, y = np.empty((n, columns), dtype=np.float64), np.empty(n, dtype=np.float64)
    digest = hashlib.sha256()
    latest = fold.train.start.isoformat()
    for i, (row, _, label) in enumerate(samples(db, fold, "train", True)):
        x[i] = (
            *transform_values({v.name: v.value for v in row.values}, state),
            float(row.horizon_days),
        )
        y[i] = cast(int, label.observed_sales_units)
        digest.update(canonical_bytes(label.model_dump(mode="json")) + b"\n")
        latest = max(latest, cast(datetime, label.label_available_at).isoformat())
    pipelines: dict[str, FunctionalPipeline] = {}
    for head in HEADS:
        name = f"models/{fold.name}/{head}.json"
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if replay is not None:
            raw = read_bytes(replay, name, MAX_MODEL_BYTES)
            decode_model_json(raw)
            pipeline = FunctionalPipeline.model_validate_json(raw)
            if (
                pipeline.head != head
                or pipeline.policy != policy
                or pipeline.preprocessing.descriptor != state.descriptor
                or pipeline.train_labels_sha256 != digest.hexdigest()
                or pipeline.code_sha256 != canonical_sha256(functional_code())
            ):
                raise SnapshotError("functional_pipeline_parent_or_code_binding")
            destination.write_bytes(raw)
            (root / (name + ".receipt")).write_bytes(read_bytes(replay, name + ".receipt"))
        else:
            fit_started = datetime.now(UTC).isoformat()
            estimator, resources = fit_worker(x, y, head, policy)
            fit_finished = datetime.now(UTC).isoformat()
            payload = {
                "schema_version": "2.0.0",
                "head": head,
                "policy": policy.model_dump(mode="json"),
                "preprocessing": state.model_dump(mode="json"),
                "train_labels_sha256": digest.hexdigest(),
                "estimator": estimator.model_dump(mode="json"),
                "code_sha256": canonical_sha256(functional_code()),
            }
            identity = payload | {"preprocessing": state.descriptor.model_dump(mode="json")}
            pipeline = FunctionalPipeline.model_validate_json(
                json.dumps(
                    payload
                    | {
                        "model_id": "model-sha256-" + canonical_sha256(identity),
                        "generated_at": datetime.now(UTC).isoformat(),
                    }
                )
            )
            write_json(destination, pipeline.model_dump(mode="json"))
            write_json(
                root / (name + ".receipt"),
                resources.model_dump(mode="json")
                | {
                    "train_rows": n,
                    "latest_label_available_at": latest,
                    "fit_call_started_at": fit_started,
                    "fit_call_finished_at": fit_finished,
                },
            )
        pipelines[head] = pipeline
    return pipelines


def predict_fold(
    db: sqlite3.Connection, fold: FoldPlan, pipelines: dict[str, FunctionalPipeline]
) -> None:
    state = pipelines["hgb_median"].preprocessing
    predictors = {name: TreePredictor(p.estimator) for name, p in pipelines.items()}

    @lru_cache(maxsize=128)
    def history(digest: str) -> HistoryContext:
        raw = db.execute("SELECT body FROM history WHERE key=?", (digest,)).fetchone()
        if raw is None:
            raise SnapshotError("functional_history_missing")
        return HistoryContext.model_validate_json(zlib.decompress(raw[0]))

    def flush(batch: list[tuple[InputRow, Membership, LabelPoint]]) -> None:
        x = np.asarray(
            [
                (
                    *transform_values({v.name: v.value for v in r.values}, state),
                    float(r.horizon_days),
                )
                for r, _, _ in batch
            ],
            dtype=np.float64,
        )
        predicted = {name: predictor.matrix(x) for name, predictor in predictors.items()}
        for i, (row, member, label) in enumerate(batch):
            points, bands = empirical_baselines(row, history(row.history_context_sha256))
            points.update(
                {
                    name: float(units[i])
                    for name, units in predicted.items()
                    if name in ("hgb_median", "rf_mean", "hgb_mean")
                }
            )
            lower, upper = float(predicted["hgb_lower"][i]), float(predicted["hgb_upper"][i])
            bands["hgb"] = (min(lower, upper), max(lower, upper))
            values = {v.name: v.value for v in row.values}
            observation = Observation(
                key=key(member).decode(),
                fold=fold.name,
                role=member.role,
                origin=row.forecast_origin.isoformat(),
                volume=volume_bin(cast(float | None, values["rolling_mean_28"]), QualityPolicy()),
                category=str(values["category_id"]),
                channel=row.channel,
                horizon=row.horizon_days,
                reasons=member.reasons,
                actual=label.observed_sales_units,
                available=label.label_available_at.isoformat()
                if label.label_available_at
                else None,
                points=points,
                bands=bands,
            )
            db.execute(
                "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)",
                (
                    observation.key,
                    fold.name,
                    member.role,
                    observation.volume,
                    observation.category,
                    observation.channel,
                    str(observation.horizon),
                    canonical_bytes(asdict(observation)),
                ),
            )

    for role in ROLES:
        batch = []
        for sample in samples(db, fold, role):
            batch.append(sample)
            if len(batch) == 256:
                flush(batch)
                batch.clear()
        if batch:
            flush(batch)
    db.commit()


def observations(
    db: sqlite3.Connection, fold: str, role: str, dimension: str = "global", value: str = "all"
) -> Iterator[Observation]:
    if dimension not in ("global", "volume", "category", "channel", "horizon"):
        raise SnapshotError("functional_invalid_dimension")
    where = "1=1" if dimension == "global" else dimension + "=?"
    parameters = (fold, fold, role) if dimension == "global" else (fold, fold, role, value)
    for (body,) in db.execute(
        "SELECT body FROM observations WHERE (?='pooled' OR fold=?) AND role=? AND "  # noqa: S608 -- dimension allowlist above; values are bound
        + where
        + " ORDER BY key",
        parameters,
    ):
        payload = json.loads(body)
        payload["reasons"] = tuple(payload["reasons"])
        payload["bands"] = {
            name: tuple(band) if band is not None else None
            for name, band in payload["bands"].items()
        }
        yield Observation(**payload)


def score(
    db: sqlite3.Connection,
    recipes: dict[str, Any],
    dimensions: dict[str, list[str]],
    policy: FunctionalPolicy,
    root: Path,
) -> dict[str, Any]:
    segments = []
    required = {"global": ["all"], "horizon": [str(i) for i in range(1, 15)], **dimensions}
    for fold in (*recipes, "pooled"):
        for role in ROLES:
            for dimension, values in required.items():
                for value in values:
                    rows = []
                    retained = True
                    for row in observations(db, fold, role, dimension, value):
                        recipe = recipes[row.fold]
                        candidate, baseline, _ = predict_pair(row, recipe)
                        rows.append(
                            ProtocolObservation(
                                key=row.key,
                                actual=row.actual,
                                exclusion_reasons=row.reasons,
                                candidate=candidate,
                                baseline=baseline,
                            )
                        )
                        group = recipe["groups"].get(
                            row.volume + ":" + row.category, recipe["groups"]["*:*"]
                        )
                        if (
                            not row.reasons
                            and group["median"]["selected"] != group["median"]["baseline"]
                        ):
                            retained = False
                    result = assess_segment_v2(
                        rows,
                        dimension=cast(
                            Literal["global", "horizon", "category", "channel", "volume"], dimension
                        ),
                        retained_median_baseline=retained,
                        policy=policy.quality,
                    )
                    segments.append(
                        {
                            "fold": fold,
                            "role": role,
                            "value": value,
                            "retained_median_baseline": retained,
                            **result,
                        }
                    )
    write_json(root / "segments.json", segments)
    counts = dict(Counter(s["status"] for s in segments))
    failures = dict(Counter(reason for s in segments for reason in s["failed_reasons"]))
    ready = all(s["status"] == "passed" for s in segments)
    return {
        "status": "passed" if ready else "not_ready",
        "segment_counts": counts,
        "failed_reasons": failures,
        "required_segment_inventory": required,
        "required_folds": [*recipes, "pooled"],
        "required_roles": list(ROLES),
        "portfolio_final_test": "not_included_not_opened",
        "legacy_results_reclassified": False,
    }


def assemble(
    features: Path,
    split_dir: Path,
    root: Path,
    policy: FunctionalPolicy,
    freeze: dict[str, Any],
    replay: Path | None = None,
) -> dict[str, Any]:
    validate_freeze(freeze, features, split_dir, policy)
    write_json(root / "progress.json", {"step": "parents", "holdout_scoring_started": False})
    with tempfile.TemporaryDirectory(prefix="functional-index-") as temporary:
        with sqlite3.connect(Path(temporary) / "index.sqlite") as db:
            feature, split, dimensions = index_parents(db, features, split_dir, policy)
            recipes = {}
            model_ids = {}
            for fold in split.descriptor.resolved_policy.folds:
                print(
                    json.dumps(
                        {
                            "step": "fit_or_replay_fold",
                            "fold": fold.name,
                            "replay": replay is not None,
                        }
                    ),
                    flush=True,
                )
                pipelines = fit_fold(db, feature, split, fold, policy, root, replay)
                model_ids[fold.name] = {
                    head: pipeline.model_id for head, pipeline in pipelines.items()
                }
                predict_fold(db, fold, pipelines)
                recipes[fold.name] = fit_recipe(
                    [r for r in observations(db, fold.name, "validation") if not r.reasons],
                    fold,
                    policy,
                )
            # All fold recipes are serialized before any evaluation of holdout errors.
            write_json(root / "recipes.json", recipes)
            write_json(
                root / "config.json",
                {
                    "policy": policy.model_dump(mode="json"),
                    "freeze": freeze,
                    "split_policy": split.descriptor.resolved_policy.model_dump(mode="json"),
                },
            )
            expected = db.execute(
                "SELECT COUNT(*) FROM members WHERE role IN ('validation','development_holdout')"
            ).fetchone()[0]
            actual = db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            if expected != actual:
                raise SnapshotError("functional_prediction_population_incomplete")
            size = 0
            with (root / "predictions.jsonl").open("wb") as output:
                for (body,) in db.execute("SELECT body FROM observations ORDER BY key"):
                    row = Observation(**json.loads(body))
                    candidate, baseline, cells = predict_pair(row, recipes[row.fold])
                    payload = {
                        "key": row.key,
                        "fold": row.fold,
                        "role": row.role,
                        "origin": row.origin,
                        "volume": row.volume,
                        "category": row.category,
                        "channel": row.channel,
                        "horizon": row.horizon,
                        "exclusion_reasons": row.reasons,
                        "candidate": candidate.model_dump(mode="json"),
                        "baseline": baseline.model_dump(mode="json"),
                        "recipe_id": recipes[row.fold]["recipe_id"],
                        "parameter_available_at": recipes[row.fold]["selection_cutoff"],
                        "evaluation_use": "validation_fit_diagnostic"
                        if row.role == "validation"
                        else "frozen_development_holdout",
                        "calibration_cells": cells,
                    }
                    encoded = canonical_bytes(payload) + b"\n"
                    size += len(encoded)
                    if size > policy.max_prediction_bytes:
                        raise SnapshotError("functional_prediction_bytes_limit")
                    output.write(encoded)
            print(
                json.dumps({"step": "all_recipes_frozen_before_scoring", "rows": actual}),
                flush=True,
            )
            write_json(root / "progress.json", {"step": "scoring", "holdout_scoring_started": True})
            gates = score(db, recipes, dimensions, policy, root)
            write_json(root / "gates.json", gates)
            write_json(
                root / "progress.json", {"step": "complete", "holdout_scoring_started": True}
            )
            descriptor = {
                "schema_version": "2.0.0",
                "model_environment": model_code().model_dump(mode="json"),
                "feature_set_id": feature.feature_set_id,
                "parent": feature.descriptor.parent.model_dump(mode="json"),
                "split_id": split.split_id,
                "label_dataset_id": split.descriptor.label_dataset_id,
                "code": functional_code(),
                "policy": policy.model_dump(mode="json"),
                "freeze": freeze,
                "model_ids": model_ids,
                "recipes": {name: r["recipe_id"] for name, r in recipes.items()},
                "prediction_rows": actual,
                "gates": gates,
            }
    receipts = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            name = path.relative_to(root).as_posix()
            size, digest = file_hash(root, name)
            receipts[name] = {"size_bytes": size, "sha256": digest}
    descriptor["files"] = receipts
    manifest = {
        "campaign_id": "forecast-functional-sha256-" + canonical_sha256(descriptor),
        "descriptor": descriptor,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    write_json(root / "functional_manifest.json", manifest)
    return manifest


def load_campaign(root: Path) -> dict[str, Any]:
    checked_directory(root)
    manifest = read_json(root, "functional_manifest.json")
    descriptor = manifest["descriptor"]
    if (
        manifest["campaign_id"] != "forecast-functional-sha256-" + canonical_sha256(descriptor)
        or descriptor["code"] != functional_code()
    ):
        raise SnapshotError("functional_manifest_identity_or_code_mismatch")
    inventory(root, {"functional_manifest.json", *descriptor["files"]})
    for name, receipt in descriptor["files"].items():
        if file_hash(root, name) != (receipt["size_bytes"], receipt["sha256"]):
            raise SnapshotError("functional_campaign_file_checksum")
    return manifest


def validate_freeze(
    freeze: dict[str, Any], features: Path, split_dir: Path, policy: FunctionalPolicy
) -> None:
    from retailops_ai.forecasting.splits import load_split

    body = {k: v for k, v in freeze.items() if k != "freeze_id"}
    if (
        freeze.get("freeze_id") != "functional-freeze-sha256-" + canonical_sha256(body)
        or freeze.get("code") != functional_code()
        or freeze.get("policy") != policy.model_dump(mode="json")
        or freeze.get("model_environment") != model_code().model_dump(mode="json")
        or freeze.get("feature_set_id") != load_feature_set(features).feature_set_id
        or freeze.get("split_id") != load_split(split_dir).split_id
    ):
        raise SnapshotError("functional_freeze_code_policy_or_parent_mismatch")


def require_unseen(output: Path, freeze: dict[str, Any]) -> None:
    """Reject refitting/requalification after this snapshot has reached test scoring."""
    if not output.exists():
        return
    for directory in output.iterdir():
        prior = None
        if (directory / "functional_manifest.json").is_file():
            prior = read_json(directory, "functional_manifest.json")["descriptor"]["freeze"]
        elif (directory / "failure.json").is_file() and (directory / "progress.json").is_file():
            if read_json(directory, "progress.json")["holdout_scoring_started"]:
                prior = read_json(directory, "failure.json")["freeze"]
        if prior is not None and (
            prior["feature_set_id"] == freeze["feature_set_id"]
            or prior.get("parent", {}).get("snapshot_id") is not None
            and prior["parent"]["snapshot_id"] == freeze.get("parent", {}).get("snapshot_id")
        ):
            raise SnapshotError(
                "functional_test_already_exposed_replay_only_requires_new_unseen_data_to_requalify"
            )


def build_campaign(
    features: Path, split_dir: Path, output: Path, policy: FunctionalPolicy, freeze: dict[str, Any]
) -> Path:
    validate_freeze(freeze, features, split_dir, policy)
    for parent in (features, split_dir):
        if output.absolute().is_relative_to(parent.absolute()):
            raise SnapshotError("functional_output_inside_parent")
    output.mkdir(parents=True, exist_ok=True)
    checked_directory(output)
    require_unseen(output, freeze)
    with tempfile.TemporaryDirectory(prefix=".functional-", dir=output) as temporary:
        root = Path(temporary)
        try:
            manifest = assemble(features, split_dir, root, policy, freeze)
        except BaseException as exc:
            write_json(
                root / "failure.json",
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "freeze": freeze,
                    "partial_artifact_not_qualified": True,
                },
            )
            publish_noreplace(root, output / ("failed-" + root.name.lstrip(".")))
            raise
        fsync_tree(root)
        destination = output / str(manifest["campaign_id"])
        publish_noreplace(root, destination)
    load_campaign(destination)
    return destination


def verify_campaign(root: Path, features: Path, split_dir: Path) -> dict[str, Any]:
    manifest = load_campaign(root)
    desc = manifest["descriptor"]
    policy = FunctionalPolicy.model_validate_json(json.dumps(desc["policy"]))
    with tempfile.TemporaryDirectory(prefix="functional-replay-") as temporary:
        replay = assemble(
            features, split_dir, Path(temporary).resolve(), policy, desc["freeze"], replay=root
        )
        if replay["descriptor"] != desc:
            raise SnapshotError("functional_independent_replay_mismatch")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("build", "verify"))
    parser.add_argument("--features", required=True, type=Path)
    parser.add_argument("--split", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--freeze", type=Path)
    args = parser.parse_args(argv)
    if args.operation == "verify":
        result = verify_campaign(args.output, args.features, args.split)
    else:
        if args.freeze is None:
            parser.error("build requires --freeze")
        freeze = json.loads(args.freeze.read_text())
        if freeze["code"] != functional_code():
            raise SnapshotError("functional_campaign_not_frozen_with_current_code")
        policy = FunctionalPolicy.model_validate_json(json.dumps(freeze["policy"]))
        result = load_campaign(
            build_campaign(args.features, args.split, args.output, policy, freeze)
        )
    print(
        json.dumps(
            {"campaign_id": result["campaign_id"], "gates": result["descriptor"]["gates"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
