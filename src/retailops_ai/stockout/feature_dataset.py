"""Seal verified causal facts in memory, then derive bounded physical features."""

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from retailops_ai.curated.builder import iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, columns_for
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    decode_json,
    read_bytes,
)
from retailops_ai.stockout.dataset import INPUT_LIMITS, implementation
from retailops_ai.stockout.feature_contract import DEFAULT_FEATURE_POLICY, FeaturePolicy
from retailops_ai.stockout.features import FEATURE_TABLES, feature_point

MAX_FEATURE_BYTES = 16 * 1024**2
MAX_FEATURE_POINTS = 10000


def feature_implementation() -> dict[str, Any]:
    from importlib.resources import files

    base = implementation()
    curated = {
        r.name: hashlib.sha256(r.read_bytes()).hexdigest()
        for r in sorted(files("retailops_ai.curated").iterdir(), key=lambda r: r.name)
        if r.is_file() and r.name.endswith(".py")
    }
    return {**base, "curated_code_sha256": hashlib.sha256(canonical_json(curated)).hexdigest()}


def load_features_input(root: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Verify the full producer/consumer contract and each selected table after reading it."""
    import tempfile

    document = verify_curated(root, limits=INPUT_LIMITS)
    if document["schema_version"] != "1.1.0" or not document["readiness"]["inventory_ready"]:
        raise SnapshotError("stockout_features_require_ready_inventory_curated_11")
    specs = {t["table"]: t for t in document["tables"]}
    records = {}
    with tempfile.TemporaryDirectory(prefix="stockout-features-seal-") as temporary:
        for name in FEATURE_TABLES:
            spec = specs[name]
            loaded = list(iter_rows(root, spec["files"], INPUT_LIMITS.batch_rows))
            digest = Digest(
                Path(temporary) / (name + ".sqlite"), columns_for(name, "1.1.0"), spec["grain"]
            )
            try:
                for row in loaded:
                    digest.add(row)
                if any(spec[k] != v for k, v in digest.summary().items()):
                    raise SnapshotError("stockout_curated_changed_during_read")
            finally:
                digest.close()
            records[name] = loaded
    return document, records


def build_features(
    curated: Path, *, policy: FeaturePolicy = DEFAULT_FEATURE_POLICY
) -> dict[str, Any]:
    document, records = load_features_input(curated)
    origins = sorted(
        {
            (r["product_id"], r["stock_location_id"], r["snapshot_at"])
            for r in records["inventory_daily_snapshots"]
        }
    )
    if len(origins) > MAX_FEATURE_POINTS:
        raise SnapshotError("stockout_feature_point_limit")
    points = [
        feature_point(records, product=p, stock=s, as_of=t, policy=policy).model_dump(mode="json")
        for p, s, t in origins
    ]
    descriptor = dict(
        schema_version="1.0.0",
        role="stockout_features",
        data_class="features",
        grain=["product_id", "stock_location_id", "as_of"],
        source_dataset_id=document["descriptor"]["parent_source_dataset_id"],
        snapshot_id=document["descriptor"]["parent_snapshot_id"],
        qualification_id=document["descriptor"]["parent_qualification_id"],
        curated_dataset_id=document["curated_dataset_id"],
        policy=policy.model_dump(mode="json"),
        implementation=feature_implementation(),
        rows=len(points),
        points_sha256=hashlib.sha256(canonical_json(points)).hexdigest(),
        limits=dict(
            input_bytes=INPUT_LIMITS.max_bytes,
            input_rows=INPUT_LIMITS.max_rows,
            output_bytes=MAX_FEATURE_BYTES,
            points=MAX_FEATURE_POINTS,
        ),
    )
    output = dict(
        feature_dataset_id="features-sha256-"
        + hashlib.sha256(canonical_json(descriptor)).hexdigest(),
        descriptor=descriptor,
        points=points,
        report=dict(
            statuses=dict(sorted(Counter(p["status"] for p in points).items())),
            reasons=dict(sorted(Counter(p["reason"] for p in points if p["reason"]).items())),
            preprocessing_fitted=False,
            upstream_forecast_ready=False,
            model_ready=False,
        ),
    )
    if len(canonical_json(output)) + 1 > MAX_FEATURE_BYTES:
        raise SnapshotError("stockout_feature_output_byte_limit")
    return output


def verify_features(target: Path, curated: Path) -> dict[str, Any]:
    stored = decode_json(
        read_bytes(target.parent, target.name, MAX_FEATURE_BYTES), limit=MAX_FEATURE_BYTES
    )
    policy = FeaturePolicy.model_validate(stored["descriptor"]["policy"])
    expected = build_features(curated, policy=policy)
    if stored != expected:
        raise SnapshotError("stockout_feature_full_replay_mismatch")
    return expected
