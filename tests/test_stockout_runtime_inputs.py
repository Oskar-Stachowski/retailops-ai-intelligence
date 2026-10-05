"""Public parent replay, missing origins, physical scope and immutable source seals."""

from datetime import datetime, timedelta

import pytest
from test_stockout_upstream_storage import bounded as bounded
from test_stockout_upstream_storage import native as native

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, read_json
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_runtime import inputs
from retailops_ai.stockout_runtime.inputs import (
    PhysicalScope,
    PreparedStockoutInputs,
    prepare_inputs,
)


@pytest.fixture(scope="module")
def prepared(bounded):
    curated, features, upstream, _, _ = bounded
    manifest = read_json(features, "manifest.json")
    first = manifest["descriptor"]["partitions"][0]
    origin = datetime.fromisoformat(first["last_as_of"])
    scope = PhysicalScope(
        product_ids=(first["product_id"],), stock_location_ids=(first["stock_location_id"],)
    )
    result = prepare_inputs(curated, features, upstream, scope=scope, as_of=origin)
    return (curated, features, upstream), scope, origin, result


def test_complete_public_replay_has_no_truth_root_and_preserves_unknown_freshness(prepared):
    roots, scope, origin, result = prepared
    assert result.scope == scope and result.as_of == origin
    assert len(result.points) == 1
    assert result.lineage.source_watermark is None
    assert result.lineage.source_completeness_status == "unavailable"
    assert result.input_role == "inference_public_facts_only"
    assert result.points[0].feature.product_id == scope.product_ids[0]
    assert result.points[0].upstream.stock_location_id == scope.stock_location_ids[0]
    assert prepare_inputs(*roots, scope=scope, as_of=origin) == result


def test_unknown_requested_date_is_not_silently_replaced_by_latest(prepared):
    roots, scope, origin, _ = prepared
    with pytest.raises(SnapshotError, match="origin_not_covered"):
        prepare_inputs(*roots, scope=scope, as_of=origin + timedelta(days=365))


def test_foreign_stock_key_does_not_receive_a_selling_location_fallback(prepared):
    roots, scope, origin, _ = prepared
    other = scope.model_copy(update={"stock_location_ids": ("foreign",)})
    with pytest.raises(SnapshotError, match="origin_not_covered"):
        prepare_inputs(*roots, scope=other, as_of=origin)


def test_naive_origin_is_rejected_before_any_parent_read(prepared, monkeypatch):
    roots, scope, origin, _ = prepared

    def refuse(*args):
        pytest.fail("parent must not be opened for an invalid origin")

    monkeypatch.setattr(inputs, "capture", refuse)
    with pytest.raises(ValueError, match="utc_timestamp"):
        prepare_inputs(*roots, scope=scope, as_of=origin.replace(tzinfo=None))


def test_resealed_capsule_cannot_hide_missing_physical_coverage(prepared):
    _, _, _, result = prepared
    body = result.model_dump(mode="json", exclude={"inputs_id"})
    body["scope"]["product_ids"].append("foreign")
    body["inputs_id"] = "stockout-inputs-sha256-" + digest(body)
    with pytest.raises(ValueError, match="identity_scope"):
        PreparedStockoutInputs.model_validate_json(canonical_json(body))


def test_resealed_capsule_cannot_claim_unmeasured_source_watermark(prepared):
    _, _, origin, result = prepared
    body = result.model_dump(mode="json", exclude={"inputs_id"})
    body["lineage"]["source_watermark"] = origin.isoformat()
    body["lineage"]["source_completeness_status"] = "complete"
    body["inputs_id"] = "stockout-inputs-sha256-" + digest(body)
    with pytest.raises(ValueError, match="identity_scope"):
        PreparedStockoutInputs.model_validate_json(canonical_json(body))


def test_source_changed_during_replay_cannot_be_registered(prepared, monkeypatch):
    roots, scope, origin, _ = prepared
    original = inputs.capture
    calls = 0

    def changed(path):
        nonlocal calls
        calls += 1
        value = original(path)
        return {**value, "unexpected": [0, "0" * 64]} if calls > 3 else value

    monkeypatch.setattr(inputs, "capture", changed)
    with pytest.raises(SnapshotError, match="public_parent_changed"):
        prepare_inputs(*roots, scope=scope, as_of=origin)


def test_duplicate_scope_is_revalidated_before_parent_read(prepared, monkeypatch):
    roots, scope, origin, _ = prepared
    invalid = scope.model_copy(update={"product_ids": (*scope.product_ids, *scope.product_ids)})

    def refuse(*args):
        pytest.fail("parent must not be opened for duplicate scope")

    monkeypatch.setattr(inputs, "capture", refuse)
    with pytest.raises(ValueError, match="duplicate_scope"):
        prepare_inputs(*roots, scope=invalid, as_of=origin)
