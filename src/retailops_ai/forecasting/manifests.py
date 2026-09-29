"""Build a formal, self-contained feature set from verified curated facts."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.contract import CalendarManifest
from retailops_ai.forecasting.features_contract import FEATURE_TYPES, HistoryContext, InputRow
from retailops_ai.forecasting.features_store import build_inputs, verify_inputs
from retailops_ai.forecasting.manifest_contract import (
    FeatureDescriptor,
    FeatureManifest,
    FeaturePolicy,
)
from retailops_ai.forecasting.manifest_io import code_pin, dump
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    read_bytes,
    read_json,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace


def load_feature_set(root: Path) -> FeatureManifest:
    raw = read_bytes(root, "feature_manifest.json")
    decode_json(raw)
    return FeatureManifest.model_validate_json(raw)


def verify_feature_set(root: Path) -> FeatureManifest:
    checked_directory(root)
    manifest = load_feature_set(root)
    inputs = verify_inputs(root / "inputs")
    desc = manifest.descriptor
    if (
        desc.inputs_id != inputs["inputs_id"]
        or desc.calendar_id != inputs["descriptor"]["calendar_id"]
        or desc.parent.model_dump(mode="json") != inputs["descriptor"]["parent"]
        or desc.input_code_sha256 != inputs["descriptor"]["implementation"]["code_sha256"]
        or desc.content_sha256 != inputs["descriptor"]["tables"]["features"]["content_sha256"]
        or desc.history_content_sha256
        != inputs["descriptor"]["tables"]["history"]["content_sha256"]
        or desc.row_count != inputs["descriptor"]["tables"]["features"]["row_count"]
    ):
        raise SnapshotError("formal_feature_inputs_binding_mismatch")
    names = {
        "feature_manifest.json",
        "inputs/inputs_manifest.json",
        "inputs/calendar_manifest.json",
    }
    names.update("inputs/" + f["path"] for spec in inputs["tables"].values() for f in spec["files"])
    inventory(root, names)
    return manifest


def build_feature_set(
    curated_dir: Path,
    calendar: CalendarManifest,
    output_root: Path,
    policy: FeaturePolicy | None = None,
) -> Path:
    output_root = output_root.absolute()
    policy = (
        FeaturePolicy()
        if policy is None
        else FeaturePolicy.model_validate_json(policy.model_dump_json())
    )
    if output_root.absolute().is_relative_to(curated_dir.absolute()):
        raise SnapshotError("formal_features_output_inside_source")
    output_root.mkdir(parents=True, exist_ok=True)
    checked_directory(output_root)
    with tempfile.TemporaryDirectory(prefix=".features-", dir=output_root) as temporary:
        root = Path(temporary)
        built = build_inputs(curated_dir, calendar, root / "work")
        built.rename(root / "inputs")
        (root / "work").rmdir()
        inputs = verify_inputs(root / "inputs")
        curated = read_json(curated_dir, "curated_manifest.json")["descriptor"]
        if canonical_sha256(curated) != calendar.descriptor.parent.curated_descriptor_sha256:
            raise SnapshotError("formal_features_source_descriptor_changed")
        descriptor = FeatureDescriptor(
            parent=calendar.descriptor.parent,
            calendar_id=calendar.calendar_id,
            inputs_id=inputs["inputs_id"],
            source_parameters=curated["source_parameters"],
            requested_policy=policy,
            resolved_policy=policy,
            feature_types={c: FEATURE_TYPES[c] for c in policy.columns},
            code=code_pin(),
            input_code_sha256=inputs["descriptor"]["implementation"]["code_sha256"],
            content_sha256=inputs["descriptor"]["tables"]["features"]["content_sha256"],
            history_content_sha256=inputs["descriptor"]["tables"]["history"]["content_sha256"],
            row_count=inputs["descriptor"]["tables"]["features"]["row_count"],
        )
        manifest = FeatureManifest(
            feature_set_id="features-sha256-"
            + canonical_sha256(descriptor.model_dump(mode="json")),
            descriptor=descriptor,
            generated_at=datetime.now(UTC),
        )
        dump(root / "feature_manifest.json", manifest)
        verify_feature_set(root)
        fsync_tree(root)
        destination = output_root / manifest.feature_set_id
        try:
            publish_noreplace(root, destination)
        except FileExistsError:
            if verify_feature_set(destination).descriptor != descriptor:
                raise SnapshotError("formal_feature_publication_conflict") from None
        return destination


def input_models(root: Path, name: str) -> Iterator[InputRow | HistoryContext]:
    """Use after verify_feature_set; stream only its pinned typed tables."""
    inputs = read_json(root / "inputs", "inputs_manifest.json")
    for ref in inputs["tables"][name]["files"]:
        if file_hash(root / "inputs", ref["path"]) != (ref["size_bytes"], ref["sha256"]):
            raise SnapshotError("formal_feature_input_changed_during_read")
        with regular_file(root / "inputs", ref["path"]) as stream:
            parquet = pq.ParquetFile(stream, pre_buffer=False, arrow_extensions_enabled=False)
            try:
                for batch in parquet.iter_batches(
                    batch_size=256, columns=["body_json"], use_threads=False
                ):
                    if batch.nbytes > 64 * 1024**2:
                        raise SnapshotError("formal_feature_input_batch_limit")
                    for row in batch.to_pylist():
                        body = row["body_json"]
                        yield (
                            InputRow.model_validate_json(body)
                            if name == "features"
                            else HistoryContext.model_validate_json(body)
                        )
            finally:
                parquet.close()
        if file_hash(root / "inputs", ref["path"]) != (ref["size_bytes"], ref["sha256"]):
            raise SnapshotError("formal_feature_input_changed_during_read")


def feature_key(row: Any) -> bytes:
    from retailops_ai.data_contracts.identity import canonical_bytes

    value = row.model_dump(mode="json")
    return canonical_bytes(
        [
            value[k]
            for k in (
                "forecast_origin",
                "product_id",
                "selling_location_id",
                "channel",
                "target_date",
            )
        ]
    )
