"""Compact forecast projection of a fully verified snapshot, without unrelated curated writes.

The factory performs the complete existing snapshot verification. Its in-process
capability cannot be reconstructed from a JSON claim of success. Only the fifteen
forecast mapping dependencies are normalized afterwards; this is a distinct
projection, never a replacement full curated dataset or a model qualification.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal, cast

from retailops_ai.curated.builder import implementation, iter_rows
from retailops_ai.curated.contract import Config, Digest, columns_for, encoded, source_contract
from retailops_ai.curated.inventory import transform_inventory
from retailops_ai.curated.transform import Index, Reject, transform
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.contract import OriginWindow, make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_contract import TABLES, HistoryContext, InputRow
from retailops_ai.forecasting.functional_recipe import empirical_baselines
from retailops_ai.forecasting.manifest_contract import (
    FeaturePolicy,
    LabelPoint,
    Membership,
    SplitPolicy,
)
from retailops_ai.forecasting.manifest_io import key
from retailops_ai.forecasting.manifests import feature_key
from retailops_ai.forecasting.quality_contract import QualityPolicy
from retailops_ai.forecasting.quality_metrics import volume_bin
from retailops_ai.forecasting.splits import count_membership, label_point, qualify
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    read_json,
    regular_file,
)
from retailops_ai.source_snapshot.importer import receipt, verify_snapshot, write_private
from retailops_ai.source_snapshot.protocol import Snapshot
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

DEPENDENCIES = tuple(
    sorted(
        set(TABLES)
        | {
            "products",
            "catalog_categories",
            "stores",
            "selling_locations",
            "stock_locations",
            "fulfillment_routes",
            "daily_demand_observations",
        }
    )
)
ROLES = ("train", "validation", "development_holdout", "purged")
MAX_PROJECTION_ROWS = 250000
MAX_PROJECTION_BYTES = 256 * 1024**2
MAX_PHYSICAL_BYTES = 2 * 1024**3
MAX_LOGICAL_BYTES = 8 * 1024**3
MAX_RECORD_BYTES = 2 * 1024**2
_VERIFIED = object()


def compact_code(version: str) -> dict[str, Any]:
    curated = implementation(version)
    hashes = dict(curated["code_files"])
    for name in (
        "functional_v12_inputs.py",
        "features.py",
        "features_contract.py",
        "contract.py",
        "functional_recipe.py",
        "splits.py",
        "manifest_contract.py",
        "manifest_io.py",
        "manifests.py",
        "quality_contract.py",
        "quality_metrics.py",
    ):
        hashes["forecasting/" + name] = hashlib.sha256(
            files("retailops_ai.forecasting").joinpath(name).read_bytes()
        ).hexdigest()
    return {
        "version": "forecast-compact-inputs-1.0.0",
        "code_files": hashes,
        "code_sha256": canonical_sha256(hashes),
        "dependency_sha256": curated["dependency_sha256"],
        "python_version": curated["python_version"],
        "pyarrow_version": curated["pyarrow_version"],
    }


def _snapshot_files(root: Path, snapshot: Snapshot) -> dict[str, Any]:
    inventory(root, snapshot.names)
    result = {
        name: {"size_bytes": size, "sha256": digest}
        for name in sorted(snapshot.names)
        for size, digest in (file_hash(root, name),)
    }
    expected = {ref["path"]: (ref["bytes"], ref["sha256"]) for ref in snapshot.references}
    expected["manifest.sha256"] = (
        65,
        hashlib.sha256((snapshot.manifest_sha256 + "\n").encode()).hexdigest(),
    )
    if result["snapshot_manifest.json"]["sha256"] != snapshot.manifest_sha256 or any(
        (result[name]["size_bytes"], result[name]["sha256"]) != ref
        for name, ref in expected.items()
    ):
        raise SnapshotError("compact_snapshot_bytes_changed_after_verification")
    return result


@dataclass(frozen=True)
class VerifiedSnapshot:
    root: Path
    snapshot: Snapshot
    seal: dict[str, Any]
    capability: object

    def verify_bytes(self) -> None:
        if self.capability is not _VERIFIED or (
            self.seal["seal_id"]
            != "forecast-source-seal-sha256-" + canonical_sha256(self.seal["descriptor"])
            or self.seal["descriptor"]["verifier"] != receipt(self.snapshot)
            or self.seal["descriptor"]["files"] != _snapshot_files(self.root, self.snapshot)
        ):
            raise SnapshotError("compact_snapshot_not_verified_or_changed")


def seal_snapshot(snapshot_root: Path, receipt_path: Path) -> VerifiedSnapshot:
    """Verify every source table, hard gate and forecast readiness once, then pin exact bytes."""
    root = checked_directory(snapshot_root)
    snapshot = verify_snapshot(
        root, allow_evaluation_truth=False, required_use_cases=("forecast_source",)
    )
    descriptor = {
        "version": "forecast-source-seal-1.0.0",
        "source_schema_version": snapshot.manifest["schema_version"],
        "source_dataset_id": snapshot.source_id,
        "snapshot_id": snapshot.snapshot_id,
        "verifier": receipt(snapshot),
        "files": _snapshot_files(root, snapshot),
        "full_source_verification": "passed",
        "required_use_cases": ["forecast_source"],
        "simulation_truth": "excluded",
    }
    seal = {
        "seal_id": "forecast-source-seal-sha256-" + canonical_sha256(descriptor),
        "descriptor": descriptor,
        "verified_at": datetime.now(UTC).isoformat(),
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    if receipt_path.exists():
        previous = read_json(receipt_path.parent, receipt_path.name)
        if previous.get("descriptor") != descriptor or previous.get("seal_id") != seal["seal_id"]:
            raise SnapshotError("compact_source_seal_conflict")
        seal = previous
    else:
        write_private(receipt_path, canonical_bytes(seal) + b"\n")
    return VerifiedSnapshot(root, snapshot, seal, _VERIFIED)


@dataclass(frozen=True)
class ForecastProjection:
    tables: dict[str, list[dict[str, Any]]]
    manifest: dict[str, Any]


def project_forecast_tables(verified: VerifiedSnapshot) -> ForecastProjection:
    """Existing mapping/normalization for all forecast dependencies; any rejection blocks."""
    verified.verify_bytes()
    snapshot, root = verified.snapshot, verified.root
    version = snapshot.manifest["schema_version"]
    specs = source_contract(version)["fact_tables"]
    source_tables = {table["table"]: table for table in snapshot.manifest["tables"]}
    if not set(DEPENDENCIES) <= set(source_tables):
        raise SnapshotError("compact_forecast_dependency_missing")
    transform_row = transform_inventory if version == "1.1.0" else transform
    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLES}
    summaries = {}
    count = logical = 0
    with tempfile.TemporaryDirectory(prefix="forecast-projection-") as temporary:
        scratch = Path(temporary)
        index = Index(scratch / "mapping.sqlite", specs)
        page = index.db.execute("PRAGMA page_size").fetchone()[0]
        index.db.execute(f"PRAGMA max_page_count={MAX_PHYSICAL_BYTES // page}")
        try:
            for name in DEPENDENCIES:
                for raw in iter_rows(root, source_tables[name]["files"], 8192):
                    count += 1
                    if count > MAX_PROJECTION_ROWS:
                        raise SnapshotError("compact_projection_dependency_row_budget")
                    index.add(name, raw)
            index.db.commit()
            for name in DEPENDENCIES:
                digest = Digest(
                    scratch / (name + ".sqlite"), columns_for(name, version), specs[name]["grain"]
                )
                try:
                    for raw in iter_rows(root, source_tables[name]["files"], 8192):
                        try:
                            row = transform_row(name, raw, specs[name]["grain"], index, Config())
                        except Reject as exc:
                            raise SnapshotError(
                                "compact_forecast_projection_rejected_" + name + "_" + str(exc)
                            ) from exc
                        digest.add(row)
                        if name in tables:
                            logical += len(encoded(row))
                            if logical > MAX_PROJECTION_BYTES:
                                raise SnapshotError("compact_projection_memory_budget")
                            tables[name].append(row)
                    summaries[name] = digest.summary()
                    if summaries[name]["row_count"] != source_tables[name]["row_count"]:
                        raise SnapshotError("compact_projection_row_reconciliation")
                finally:
                    digest.close()
        finally:
            index.close()
    verified.verify_bytes()
    descriptor = {
        "version": "forecast-curated-projection-1.0.0",
        "source_seal_id": verified.seal["seal_id"],
        "source_dataset_id": snapshot.source_id,
        "snapshot_id": snapshot.snapshot_id,
        "forecast_tables": list(TABLES),
        "mapping_dependencies": list(DEPENDENCIES),
        "tables": summaries,
        "rejected_rows": 0,
        "normalized_forecast_bytes": logical,
        "config": Config().document(),
        "code": compact_code(version),
        "forecast_source": "passed",
        "scope": "forecast_only_projection_not_full_curated_inventory_qualification",
    }
    return ForecastProjection(
        tables,
        {
            "projection_id": "forecast-projection-sha256-" + canonical_sha256(descriptor),
            "descriptor": descriptor,
        },
    )


def origin_panel(
    tables: dict[str, list[dict[str, Any]]], day: date
) -> Iterator[tuple[HistoryContext, tuple[InputRow, ...]]]:
    """Exact existing active-panel construction, including all known target exclusions."""
    view = OriginFeatures(tables, make_origin(day))
    if len(view.assortment) > 10000:
        raise SnapshotError("forecast_series_limit")
    for series in sorted(view.assortment):
        history = view.history(series)
        targets = view.targets(history)
        if history.points or targets:
            yield history, tuple(targets)


def compact_feature(row: InputRow, history: HistoryContext) -> dict[str, Any]:
    points, bands = empirical_baselines(row, history)
    values = {value.name: value.value for value in row.values}
    references = {
        canonical_sha256(ref.model_dump(mode="json")): ref.model_dump(mode="json")
        for value in row.values
        for ref in value.references
    }
    available = [ref.available_at for value in row.values for ref in value.references]
    available += [
        value.source_available_at for value in row.values if value.source_available_at is not None
    ]
    return {
        "feature_key": feature_key(row).decode(),
        "origin": row.forecast_origin.isoformat(),
        "product_id": row.product_id,
        "selling_location_id": row.selling_location_id,
        "channel": row.channel,
        "target_date": row.target_date.isoformat(),
        "horizon": row.horizon_days,
        "feature_values": values,
        "feature_statuses": {v.name: v.status for v in row.values},
        "category": str(values["category_id"]),
        "volume": volume_bin(cast(float | None, values["rolling_mean_28"]), QualityPolicy()),
        "baseline_points": points,
        "baseline_bands": bands,
        "input_row_sha256": canonical_sha256(row.model_dump(mode="json")),
        "history_context_sha256": history.content_sha256(),
        "pit": {
            "max_available_at": max(available).isoformat() if available else None,
            "reference_count": len(references),
            "references_sha256": canonical_sha256(references),
            "proof": "typed_InputRow_full_hash_plus_history_and_sealed_source_replay",
        },
    }


class _Writer:
    def __init__(self, root: Path, name: str, budget: Counter[str]) -> None:
        self.root, self.name, self.budget = root, name, budget
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("xb")
        self.stream = gzip.GzipFile(
            filename="", mode="wb", fileobj=self.file, mtime=0, compresslevel=3
        )
        self.hash = hashlib.sha256()
        self.rows = 0

    def add(self, value: dict[str, Any]) -> None:
        raw = canonical_bytes(value) + b"\n"
        self.budget["logical_bytes"] += len(raw)
        if len(raw) > MAX_RECORD_BYTES or self.budget["logical_bytes"] > MAX_LOGICAL_BYTES:
            raise SnapshotError("compact_output_logical_budget")
        self.hash.update(raw)
        self.stream.write(raw)
        self.rows += 1

    def close(self) -> None:
        self.stream.close()
        self.file.close()

    def summary(self) -> dict[str, Any]:
        self.close()
        size, digest = file_hash(self.root, self.name)
        self.budget["physical_bytes"] += size
        if self.budget["physical_bytes"] > MAX_PHYSICAL_BYTES:
            raise SnapshotError("compact_output_physical_budget")
        return {
            "path": self.name,
            "size_bytes": size,
            "sha256": digest,
            "row_count": self.rows,
            "content_sha256": self.hash.hexdigest(),
        }


def _write_projection_inputs(
    projection: ForecastProjection,
    root: Path,
    split: SplitPolicy,
    window: OriginWindow,
    feature_policy: FeaturePolicy,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if any(
        f.train.start < window.start or f.development_holdout.end > window.end for f in split.folds
    ):
        raise SnapshotError("compact_split_outside_origin_window")
    candidates: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for observation in projection.tables["daily_demand_versions"]:
        candidates[
            tuple(
                observation[k]
                for k in ("business_date", "product_id", "selling_location_id", "channel")
            )
        ].append(observation)
    budget: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    summaries = {}
    origins = {}
    with ExitStack() as stack:
        members = {}
        for fold in split.folds:
            for role in ROLES:
                writer = _Writer(root, f"memberships/{fold.name}/{role}.jsonl.gz", budget)
                stack.callback(writer.close)
                members[fold.name, role] = writer
        for offset in range((window.end - window.start).days + 1):
            day = window.start + timedelta(days=offset)
            features = _Writer(root, f"features/{day.isoformat()}.jsonl.gz", budget)
            histories = _Writer(root, f"histories/{day.isoformat()}.jsonl.gz", budget)
            try:
                for history, rows in origin_panel(projection.tables, day):
                    histories.add(history.model_dump(mode="json"))
                    counts["histories"] += 1
                    for row in rows:
                        packed = compact_feature(row, history)
                        features.add(packed)
                        counts["features"] += 1
                        for fold in split.folds:
                            role = fold.role(day)
                            label = (
                                None
                                if role == "purged"
                                else label_point(
                                    row,
                                    fold,
                                    candidates[
                                        row.target_date,
                                        row.product_id,
                                        row.selling_location_id,
                                        row.channel,
                                    ],
                                )
                            )
                            member = qualify(row, history, fold, feature_policy, label)
                            count_membership(counts, member)
                            counts["memberships"] += 1
                            counts["labels"] += label is not None
                            members[fold.name, role].add(
                                {
                                    "feature_key": packed["feature_key"],
                                    "membership": member.model_dump(mode="json"),
                                    "label": label.model_dump(mode="json") if label else None,
                                }
                            )
                for writer in (features, histories):
                    summaries[writer.name] = writer.summary()
                origins[day.isoformat()] = {"features": features.name, "histories": histories.name}
            finally:
                features.close()
                histories.close()
            if progress is not None and (offset % 10 == 0 or day == window.end):
                progress(
                    {"stage": "compact_origins", "origin": day.isoformat(), "counts": dict(counts)}
                )
        for writer in members.values():
            summaries[writer.name] = writer.summary()
    if counts["features"] == 0 or counts["memberships"] != counts["features"] * len(split.folds):
        raise SnapshotError("compact_panel_membership_reconciliation")
    ready = not counts["stale_history"] and all(
        counts[f.name + ":" + role + ":eligible"] >= split.minimum_eligible_rows_per_role
        for f in split.folds
        for role in ROLES[:3]
    )
    return {
        "origins": origins,
        "files": summaries,
        "counts": dict(counts),
        "bytes": dict(budget),
        "split_qualification_status": "passed" if ready else "not_ready",
    }


def build_compact_inputs(
    verified: VerifiedSnapshot,
    output: Path,
    split: SplitPolicy,
    *,
    origin_window: OriginWindow,
    feature_policy: FeaturePolicy | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    verified.verify_bytes()
    if output.absolute().is_relative_to(verified.root.absolute()):
        raise SnapshotError("compact_output_inside_source")
    output.mkdir(parents=True, exist_ok=True)
    checked_directory(output)
    if (
        min(shutil.disk_usage(output).free, shutil.disk_usage(tempfile.gettempdir()).free)
        < 4 * 1024**3
    ):
        raise SnapshotError("compact_projection_and_output_space_budget")
    projection = project_forecast_tables(verified)
    if progress is not None:
        progress(
            {
                "stage": "compact_projection_verified",
                "projection_id": projection.manifest["projection_id"],
            }
        )
    with tempfile.TemporaryDirectory(prefix=".compact-inputs-", dir=output) as temporary:
        root = Path(temporary)
        try:
            report = _write_projection_inputs(
                projection, root, split, origin_window, feature_policy or FeaturePolicy(), progress
            )
            verified.verify_bytes()
            descriptor = {
                "version": "forecast-compact-inputs-1.0.0",
                "source_seal": {
                    name: value for name, value in verified.seal.items() if name != "verified_at"
                },
                "projection": projection.manifest,
                "split_policy": split.model_dump(mode="json"),
                "origin_window": origin_window.model_dump(mode="json"),
                "feature_policy": (feature_policy or FeaturePolicy()).model_dump(mode="json"),
                "forecast_model_status": "not_ready",
                "holdout_metrics_evaluated": False,
                "roles": list(ROLES),
                "daily_horizons": list(range(1, 15)),
                "code": compact_code(verified.snapshot.manifest["schema_version"]),
                **report,
            }
            manifest = {
                "inputs_id": "forecast-compact-inputs-sha256-" + canonical_sha256(descriptor),
                "descriptor": descriptor,
                "built_at": datetime.now(UTC).isoformat(),
            }
            write_private(root / "compact_manifest.json", canonical_bytes(manifest) + b"\n")
            fsync_tree(root)
            destination = output / str(manifest["inputs_id"])
            if destination.exists():
                if load_compact_inputs(destination)["descriptor"] != descriptor:
                    raise SnapshotError("compact_immutable_publication_conflict")
            else:
                publish_noreplace(root, destination)
        except BaseException as exc:
            write_private(
                root / "failure.json",
                canonical_bytes(
                    {
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "source_seal_id": verified.seal["seal_id"],
                        "partial_artifact_not_qualified": True,
                    }
                )
                + b"\n",
            )
            publish_noreplace(root, output / ("failed-" + root.name.lstrip(".")))
            raise
    return destination


def load_compact_inputs(root: Path) -> dict[str, Any]:
    manifest = read_json(root, "compact_manifest.json")
    desc = manifest["descriptor"]
    seal = desc["source_seal"]
    projection = desc["projection"]
    version = seal["descriptor"]["source_schema_version"]
    if (
        manifest["inputs_id"] != "forecast-compact-inputs-sha256-" + canonical_sha256(desc)
        or desc["roles"] != list(ROLES)
        or desc["daily_horizons"] != list(range(1, 15))
        or desc["forecast_model_status"] != "not_ready"
        or desc["holdout_metrics_evaluated"] is not False
        or desc["code"] != compact_code(version)
        or projection["descriptor"]["code"] != desc["code"]
        or projection["projection_id"]
        != "forecast-projection-sha256-" + canonical_sha256(projection["descriptor"])
        or seal["seal_id"] != "forecast-source-seal-sha256-" + canonical_sha256(seal["descriptor"])
        or projection["descriptor"]["source_seal_id"] != seal["seal_id"]
        or desc["counts"]["memberships"]
        != desc["counts"]["features"] * len(desc["split_policy"]["folds"])
    ):
        raise SnapshotError("compact_input_identity_or_scope_mismatch")
    inventory(root, {"compact_manifest.json", *desc["files"]})
    for name, ref in desc["files"].items():
        if name != ref["path"] or file_hash(root, name) != (ref["size_bytes"], ref["sha256"]):
            raise SnapshotError("compact_input_file_checksum")
    return manifest


def replay_compact_inputs(root: Path, verified: VerifiedSnapshot) -> dict[str, Any]:
    """Independently rebuild the compact projection/panel from the sealed source, without fits."""
    manifest = load_compact_inputs(root)
    desc = manifest["descriptor"]
    verified.verify_bytes()
    if desc["source_seal"]["descriptor"] != verified.seal["descriptor"]:
        raise SnapshotError("compact_replay_source_seal_mismatch")
    with tempfile.TemporaryDirectory(prefix="forecast-compact-replay-") as temporary:
        rebuilt = build_compact_inputs(
            verified,
            Path(temporary),
            SplitPolicy.model_validate_json(json.dumps(desc["split_policy"])),
            origin_window=OriginWindow.model_validate_json(json.dumps(desc["origin_window"])),
            feature_policy=FeaturePolicy.model_validate_json(json.dumps(desc["feature_policy"])),
        )
        replay = load_compact_inputs(rebuilt)
        if replay["descriptor"] != desc or replay["inputs_id"] != manifest["inputs_id"]:
            raise SnapshotError("compact_independent_replay_mismatch")
    return manifest


def _read_records(root: Path, name: str, ref: dict[str, Any]) -> Iterator[dict[str, Any]]:
    if file_hash(root, name) != (ref["size_bytes"], ref["sha256"]):
        raise SnapshotError("compact_input_file_checksum")
    digest, count = hashlib.sha256(), 0
    with regular_file(root, name) as raw, gzip.GzipFile(fileobj=raw, mode="rb") as stream:
        while line := stream.readline(MAX_RECORD_BYTES + 1):
            if len(line) > MAX_RECORD_BYTES or not line.endswith(b"\n"):
                raise SnapshotError("compact_input_record_budget")
            digest.update(line)
            count += 1
            if count > ref["row_count"]:
                raise SnapshotError("compact_input_record_count")
            yield decode_json(line)
    if count != ref["row_count"] or digest.hexdigest() != ref["content_sha256"]:
        raise SnapshotError("compact_input_logical_checksum")


def iter_compact_rows(
    root: Path,
    manifest: dict[str, Any],
    fold_name: str,
    role: Literal["train", "validation", "development_holdout", "purged"],
) -> Iterator[dict[str, Any]]:
    """Bounded origin-at-a-time feature join; labels/exclusions retain exact original contracts."""
    if role not in ROLES:
        raise SnapshotError("compact_unknown_role")
    desc = manifest["descriptor"]
    split = SplitPolicy.model_validate_json(json.dumps(desc["split_policy"]))
    fold = next((f for f in split.folds if f.name == fold_name), None)
    if fold is None:
        raise SnapshotError("compact_unknown_fold")
    name = f"memberships/{fold_name}/{role}.jsonl.gz"
    current: str | None = None
    features: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    read_count = 0
    for record in _read_records(root, name, desc["files"][name]):
        member = Membership.model_validate_json(json.dumps(record["membership"]))
        label = (
            LabelPoint.model_validate_json(json.dumps(record["label"]))
            if record["label"] is not None
            else None
        )
        day = member.forecast_origin.date().isoformat()
        if day != current:
            if current is not None and day <= current:
                raise SnapshotError("compact_membership_origin_order")
            if current is not None and len(seen) != len(features):
                raise SnapshotError("compact_membership_panel_incomplete")
            current = day
            part = desc["origins"][day]["features"]
            features = {}
            for item in _read_records(root, part, desc["files"][part]):
                if item["feature_key"] in features:
                    raise SnapshotError("compact_duplicate_feature_key")
                features[item["feature_key"]] = item
                if len(features) > 140000:
                    raise SnapshotError("compact_origin_feature_budget")
            seen.clear()
        fkey = record["feature_key"]
        if fkey in seen or fkey not in features or feature_key(member).decode() != fkey:
            raise SnapshotError("compact_membership_feature_binding")
        seen.add(fkey)
        read_count += 1
        feature = features[fkey]
        if (
            member.fold != fold_name
            or member.role != role
            or fold.role(member.forecast_origin.date()) != role
        ):
            raise SnapshotError("compact_membership_role_binding")
        if member.label_content_sha256 != (
            canonical_sha256(label.model_dump(mode="json")) if label else None
        ):
            raise SnapshotError("compact_label_content_binding")
        if (
            (role == "purged") != (label is None)
            or label is not None
            and (key(label) != key(member) or label.knowledge_cutoff != fold.label_cutoff(role))
        ):
            raise SnapshotError("compact_label_role_or_cutoff")
        if member.eligible and (
            label is None
            or label.status != "eligible"
            or label.label_available_at is None
            or label.label_available_at > fold.label_cutoff(role)
        ):
            raise SnapshotError("compact_label_not_available_at_role_cutoff")
        if (
            feature["pit"]["max_available_at"] is not None
            and datetime.fromisoformat(feature["pit"]["max_available_at"]) > member.forecast_origin
        ):
            raise SnapshotError("compact_future_feature")
        yield {
            **feature,
            "key": key(member).decode(),
            "fold": fold_name,
            "role": role,
            "eligible": member.eligible,
            "reasons": list(member.reasons),
            "actual": label.observed_sales_units if label and member.eligible else None,
            "label_available_at": label.label_available_at.isoformat()
            if label and label.label_available_at and member.eligible
            else None,
            "membership": record["membership"],
            "label": record["label"],
        }
    expected = sum(
        desc["files"][parts["features"]]["row_count"]
        for day, parts in desc["origins"].items()
        if fold.role(date.fromisoformat(day)) == role
    )
    if read_count != expected or current is not None and len(seen) != len(features):
        raise SnapshotError("compact_membership_panel_incomplete")
