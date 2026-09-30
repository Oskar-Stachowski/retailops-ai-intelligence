"""Self-contained, bounded inference inputs from verified AI04 feature and curated packages."""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from retailops_ai.curated.builder import verify_curated
from retailops_ai.data_contracts.common import Contract, UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs.contracts import BatchScope, ProfileID
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_contract import TABLES, HistoryContext, InputRow
from retailops_ai.forecasting.manifest_contract import FeatureManifest
from retailops_ai.forecasting.manifests import input_models, verify_feature_set
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    inventory,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

MAX_INPUT_BYTES = 32 * 1024**2


def series(row: HistoryContext | InputRow) -> tuple[str, str, str]:
    return row.product_id, row.selling_location_id, row.channel


def scoped_inputs(
    profile: "PreparedInputs", scope: BatchScope, horizon: Literal[7, 14]
) -> "PreparedInputs":
    """Derive a content-addressed subset; the queue retains the original registration pin."""
    profile = PreparedInputs.model_validate_json(profile.model_dump_json())
    if (
        not set(scope.product_ids) <= set(profile.scope.product_ids)
        or not set(scope.selling_location_ids) <= set(profile.scope.selling_location_ids)
        or scope.channel != profile.scope.channel
        or horizon > profile.horizon_days
    ):
        raise ValueError("prepared_inputs_scope_or_horizon_uncovered")
    keys = {
        (p, loc, scope.channel) for p in scope.product_ids for loc in scope.selling_location_ids
    }
    return prepared(
        InputContent(
            feature_manifest=profile.feature_manifest,
            as_of_time=profile.as_of_time,
            scope=scope,
            horizon_days=horizon,
            rows=tuple(r for r in profile.rows if series(r) in keys and r.horizon_days <= horizon),
            histories=tuple(h for h in profile.histories if series(h) in keys),
        )
    )


class InputContent(Contract):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["forecast_inference_inputs"] = "forecast_inference_inputs"
    feature_manifest: FeatureManifest
    as_of_time: UtcTime
    scope: BatchScope
    horizon_days: Literal[7, 14]
    rows: tuple[InputRow, ...] = Field(min_length=1, max_length=1400)
    histories: tuple[HistoryContext, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def coherence(self) -> Self:
        if self.as_of_time.time().isoformat() != "23:59:59":
            raise ValueError("inference_origin_must_close_utc_day")
        expected = {
            (p, loc, self.scope.channel, h)
            for p in self.scope.product_ids
            for loc in self.scope.selling_location_ids
            for h in range(1, self.horizon_days + 1)
        }
        keys = [(*series(r), r.horizon_days) for r in self.rows]
        if keys != sorted(expected) or any(r.forecast_origin != self.as_of_time for r in self.rows):
            raise ValueError("inference_inputs_incomplete_or_unordered_grain")
        contexts = {series(h): h for h in self.histories}
        if len(contexts) != len(self.histories) or list(contexts) != sorted(
            {k[:3] for k in expected}
        ):
            raise ValueError("inference_history_scope_or_order_mismatch")
        view = OriginFeatures({t: [] for t in TABLES}, make_origin(self.as_of_time.date()))
        observed = {k: view.historical_values(h) for k, h in contexts.items()}
        for row in self.rows:
            history = contexts[series(row)]
            if (
                history.forecast_origin != self.as_of_time
                or history.content_sha256() != row.history_context_sha256
                or row.history_active_days != len(history.points)
                or row.history_known_days != sum(p.status != "missing" for p in history.points)
                or row.history_closed_days != sum(p.status == "closed" for p in history.points)
                or {v.name: v for v in row.values if v.kind == "observed"} != observed[series(row)]
            ):
                raise ValueError("inference_history_statistics_binding_mismatch")
        return self


class PreparedInputs(InputContent):
    profile_id: ProfileID

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.profile_id != "batch-profile-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"profile_id"})
        ):
            raise ValueError("inference_inputs_identity_mismatch")
        return self


def prepared(content: InputContent) -> PreparedInputs:
    raw = content.model_dump(mode="json")
    raw["profile_id"] = "batch-profile-sha256-" + canonical_sha256(raw)
    return PreparedInputs.model_validate_json(json.dumps(raw))


def verify_inputs_package(root: Path) -> PreparedInputs:
    checked_directory(root)
    inventory(root, {"inputs.json"})
    with regular_file(root, "inputs.json") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise SnapshotError("inference_inputs_byte_limit")
    # Duplicate keys/nonfinite numbers are rejected before semantic validation.
    from retailops_ai.security.local import strict_json

    strict_json(raw)
    return PreparedInputs.model_validate_json(raw)


def build_inputs_package(
    feature_dir: Path,
    curated_dir: Path,
    output_root: Path,
    *,
    as_of: datetime,
    scope: BatchScope,
    horizon_days: Literal[7, 14],
) -> Path:
    as_of = TypeAdapter(UtcTime).validate_python(as_of)
    scope = BatchScope.model_validate_json(scope.model_dump_json())
    if as_of.time().isoformat() != "23:59:59" or horizon_days not in (7, 14):
        raise SnapshotError("inference_origin_or_horizon_invalid")
    if as_of > datetime.now(UTC):
        raise SnapshotError("inference_inputs_from_future")
    features = verify_feature_set(feature_dir)
    curated = verify_curated(curated_dir)
    parent = features.descriptor.parent
    if (
        curated["curated_dataset_id"] != parent.curated_dataset_id
        or curated["descriptor"]["parent_source_dataset_id"] != parent.source_dataset_id
        or canonical_sha256(curated["descriptor"]) != parent.curated_descriptor_sha256
        or curated["readiness"]["forecast_source"] != "passed"
    ):
        raise SnapshotError("inference_curated_feature_parent_mismatch")
    wanted = {
        (p, loc, scope.channel) for p in scope.product_ids for loc in scope.selling_location_ids
    }
    rows, histories = [], []
    logical_bytes = 0
    for name in ("features", "history"):
        for row in input_models(feature_dir, name):
            if row.forecast_origin != as_of or series(row) not in wanted:
                continue
            if isinstance(row, InputRow):
                if row.horizon_days > horizon_days:
                    continue
                rows.append(row)
            else:
                histories.append(row)
            logical_bytes += len(row.model_dump_json().encode())
            if logical_bytes > MAX_INPUT_BYTES or len(rows) > 1400 or len(histories) > 100:
                raise SnapshotError("inference_inputs_resource_limit")
    # Refuse a package changed during either stream, even if each partition was coherent.
    if verify_feature_set(feature_dir) != features:
        raise SnapshotError("inference_features_changed_during_preparation")
    value = prepared(
        InputContent(
            feature_manifest=features,
            as_of_time=as_of,
            scope=scope,
            horizon_days=horizon_days,
            rows=tuple(sorted(rows, key=lambda r: (*series(r), r.horizon_days))),
            histories=tuple(sorted(histories, key=series)),
        )
    )
    if any(output_root.absolute().is_relative_to(p.absolute()) for p in (feature_dir, curated_dir)):
        raise SnapshotError("inference_output_inside_immutable_input")
    return publish_inputs_package(value, output_root)


def publish_inputs_package(value: PreparedInputs, output_root: Path) -> Path:
    value = PreparedInputs.model_validate_json(value.model_dump_json())
    raw = canonical_bytes(value.model_dump(mode="json")) + b"\n"
    if len(raw) > MAX_INPUT_BYTES:
        raise SnapshotError("inference_inputs_byte_limit")
    output_root = output_root.absolute()
    output_root.mkdir(parents=True, exist_ok=True)
    checked_directory(output_root)
    with tempfile.TemporaryDirectory(prefix=".inference-", dir=output_root) as temporary:
        staging = Path(temporary)
        staging.chmod(0o700)
        (staging / "inputs.json").write_bytes(raw)
        (staging / "inputs.json").chmod(0o600)
        verify_inputs_package(staging)
        fsync_tree(staging)
        destination = output_root / value.profile_id
        try:
            publish_noreplace(staging, destination)
        except FileExistsError:
            if verify_inputs_package(destination) != value:
                raise SnapshotError("inference_inputs_publication_conflict") from None
        return destination
