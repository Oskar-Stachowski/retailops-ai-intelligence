"""Both sealed public formats route to complete replay, without changing model code."""

import shutil
from datetime import datetime

import pytest
from test_stockout_upstream_series import native as native
from test_stockout_upstream_series import series_bundle as series_bundle

from retailops_ai.source_snapshot.files import SnapshotError, read_json
from retailops_ai.stockout_public_inputs import implementation, prepare_inputs
from retailops_ai.stockout_runtime.inputs import PhysicalScope
from retailops_ai.stockout_upstream_storage.bundle import build_upstream_bundle


@pytest.fixture(scope="module")
def prepared(series_bundle):
    curated, features, upstream, _, _ = series_bundle
    part = read_json(features, "manifest.json")["descriptor"]["partitions"][0]
    origin = datetime.fromisoformat(part["last_as_of"])
    scope = PhysicalScope(
        product_ids=(part["product_id"],), stock_location_ids=(part["stock_location_id"],)
    )
    return (curated, features, upstream), scope, origin


def test_series_public_inputs_match_legacy_scoring_points_and_keep_full_lineage(prepared, tmp_path):
    roots, scope, origin = prepared
    legacy = tmp_path / "upstream20"
    build_upstream_bundle(roots[0], roots[1], legacy)
    old = prepare_inputs(roots[0], roots[1], legacy, scope=scope, as_of=origin)
    new = prepare_inputs(*roots, scope=scope, as_of=origin)
    assert new.points == old.points
    assert new.scope == old.scope and new.as_of == old.as_of
    assert new.lineage.feature_set_id == old.lineage.feature_set_id
    assert new.lineage.upstream_bundle_id != old.lineage.upstream_bundle_id
    assert new.preparation_code_sha256 == implementation()
    assert new.lineage.source_watermark is None
    assert new.parent_replay == "complete_public_features_and_upstream"
    assert new.inputs_id != old.inputs_id


def test_series_tamper_in_an_unselected_part_blocks_input_registration(prepared, tmp_path):
    roots, scope, origin = prepared
    copy = tmp_path / "tampered"
    shutil.copytree(roots[2], copy)
    parts = read_json(copy, "manifest.json")["descriptor"]["partitions"]
    target = copy / parts[-1]["path"]
    target.write_bytes(target.read_bytes() + b"changed")
    with pytest.raises(SnapshotError, match="full_replay_mismatch"):
        prepare_inputs(roots[0], roots[1], copy, scope=scope, as_of=origin)


def test_invalid_scope_is_rejected_before_version_routing(prepared, monkeypatch):
    roots, scope, origin = prepared
    broken = scope.model_copy(update={"product_ids": (*scope.product_ids, *scope.product_ids)})

    def refuse(*args):
        pytest.fail("invalid scope must not open public parents")

    monkeypatch.setattr("retailops_ai.stockout_public_inputs.read_json", refuse)
    with pytest.raises(ValueError, match="duplicate_scope"):
        prepare_inputs(*roots, scope=broken, as_of=origin)
