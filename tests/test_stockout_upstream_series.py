"""Physical selection cannot hide foreign ambiguities, stale routes or future information."""

import hashlib
import shutil
import sqlite3
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_stockout_features import native as native
from test_stockout_upstream import AS_OF, add_channel
from test_stockout_upstream import routed as routed
from test_stockout_upstream import tables as tables
from test_stockout_upstream_storage import json_points, physical

from retailops_ai.curated.contract import encoded
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, read_json
from retailops_ai.stockout.feature_dataset import load_features_input, read_sealed_tables
from retailops_ai.stockout.upstream import UPSTREAM_TABLES, upstream_point
from retailops_ai.stockout.upstream_dataset import build_upstream
from retailops_ai.stockout_history.bundle import build_history_bundle
from retailops_ai.stockout_upstream_series import bundle, store
from retailops_ai.stockout_upstream_series.bundle import (
    build_upstream_bundle,
    iter_verified_upstream,
    verify_upstream_bundle,
)
from retailops_ai.stockout_upstream_series.store import SeriesFacts, UpstreamStoragePolicy
from retailops_ai.stockout_upstream_storage.store import UpstreamFacts, keys


@pytest.fixture(scope="module")
def series_bundle(native, tmp_path_factory):
    _, curated, features, _ = native
    root = tmp_path_factory.mktemp("upstream-series")
    parent, target = root / "features", root / "upstream"
    build_history_bundle(curated.directory, parent)
    doc, status = build_upstream_bundle(curated.directory, parent, target)
    assert status == "published"
    return curated.directory, parent, target, doc, build_upstream(curated.directory, features)


def test_native_complete_parity_reader_replay_retry_and_private_files(series_bundle):
    curated, parent, target, doc, old = series_bundle
    rows = json_points(target, doc)
    assert physical(rows) == old["points"]
    assert doc["descriptor"]["points_sha256"] == hashlib.sha256(canonical_json(rows)).hexdigest()
    assert doc["descriptor"]["schema_version"] == "2.1.0"
    assert verify_upstream_bundle(target, curated, parent) == doc
    assert build_upstream_bundle(curated, parent, target) == (doc, "reused")
    assert [
        p.model_dump(mode="json") for p in iter_verified_upstream(target, curated, parent)
    ] == rows
    assert all(doc["report"][k] == v for k, v in old["report"].items())
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in target.iterdir())


def test_each_native_physical_origin_matches_full_panel_and_selection_is_smaller(native, tmp_path):
    _, curated, features, _ = native
    doc, _ = load_features_input(curated.directory)
    full = read_sealed_tables(curated.directory, doc, UPSTREAM_TABLES)
    with SeriesFacts(curated.directory, scratch=tmp_path) as facts:
        for point in features["points"]:
            at = datetime.fromisoformat(point["as_of"].replace("Z", "+00:00"))
            product, stock = point["product_id"], point["stock_location_id"]
            selected = facts.known_series(product, stock, make_origin(at.date()).forecast_origin)
            assert upstream_point(
                selected, product=product, stock=stock, as_of=at
            ) == upstream_point(full, product=product, stock=stock, as_of=at)
        assert facts.stats["maximum_selected_rows"] < facts.stats["stored_rows"]
        assert facts.stats["globally_validated_origins"] > 0
        path = facts.path
        assert path.stat().st_mode & 0o777 == 0o600
    assert not path.parent.exists()
    with pytest.raises(SnapshotError, match="closed"):
        facts.known_series("x", "y", at)


def synthetic(records, tmp_path, monkeypatch, policy=store.DEFAULT_STORAGE_POLICY):
    """Use production index/selection over sparse projection rows, bypassing source I/O only."""

    def columns(table, version):
        rows = records[table]
        result = []
        for name in sorted({k for r in rows for k in r}):
            sample = next((r.get(name) for r in rows if r.get(name) is not None), None)
            kind = (
                "timestamp[us, tz=UTC]"
                if isinstance(sample, datetime)
                else "date32[day]"
                if isinstance(sample, date)
                else "decimal128(18, 2)"
                if isinstance(sample, Decimal)
                else "string"
            )
            if kind != "string":
                result.append({"name": name, "type": kind})
        return result

    def open_facts(self):
        self.path = tmp_path / "synthetic.sqlite"
        self.path.touch(mode=0o600, exist_ok=False)
        db = sqlite3.connect(self.path)
        self._db = db
        self._stack.callback(db.close)
        db.execute(
            "CREATE TABLE facts(table_name TEXT, position INTEGER, product TEXT, stock TEXT, ready TEXT, payload BLOB, checksum TEXT, PRIMARY KEY(table_name,position))"
        )
        for name in UPSTREAM_TABLES:
            for position, row in enumerate(records[name]):
                raw = encoded(row)
                db.execute(
                    "INSERT INTO facts VALUES (?,?,?,?,?,?,?)",
                    (name, position, *keys(name, row), raw, hashlib.sha256(raw).hexdigest()),
                )
                self.stats["stored_rows"] += 1
        db.commit()
        self.document = {"curated_dataset_id": "synthetic"}
        self.seal = {"synthetic": True}
        db.execute("PRAGMA query_only=ON")

    monkeypatch.setattr(UpstreamFacts, "_open", open_facts)
    monkeypatch.setattr(store, "columns_for", columns)
    return SeriesFacts(Path("unused"), policy=policy)


def projected(records, facts):
    selected = facts.known_series("p-1", "stock", make_origin(AS_OF.date()).forecast_origin)
    point = upstream_point(selected, product="p-1", stock="stock", as_of=AS_OF)
    assert point == upstream_point(records, product="p-1", stock="stock", as_of=AS_OF)
    return point


@pytest.mark.parametrize("table", list(store.LOGICAL_KEYS))
def test_foreign_highest_known_version_ties_are_not_hidden(routed, tmp_path, monkeypatch, table):
    if not routed[table]:
        routed[table].append({**routed["price_plans"][0], "promotion_key": "promotion"})
    foreign = deepcopy(routed[table][0])
    # Leave the repeated logical key outside the selected physical series.
    if table == "product_catalog":
        foreign["id"] = "foreign"
    elif "product_id" in foreign:
        foreign["product_id"] = "foreign"
    elif "selling_location_id" in foreign:
        foreign["selling_location_id"] = "foreign"
    else:
        foreign["category_id"] = "foreign"
    for key in ("assignment_key", "assortment_key", "plan_key", "promotion_key", "route_key"):
        if key in foreign:
            foreign[key] = "foreign"
    routed[table].extend([foreign, deepcopy(foreign)])
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        with pytest.raises((SnapshotError, ValueError), match="ambiguous"):
            facts.known_series("p-1", "stock", make_origin(AS_OF.date()).forecast_origin)


def test_foreign_route_overlap_rejected_before_physical_filter(routed, tmp_path, monkeypatch):
    routed["fulfillment_routes"].append(
        {
            **routed["fulfillment_routes"][0],
            "route_key": "foreign",
            "stock_location_id": "elsewhere",
        }
    )
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        with pytest.raises(SnapshotError, match="ambiguous_physical"):
            projected(routed, facts)


@pytest.mark.parametrize(
    "versions,rejected", [([1, 1, 2], True), ([2, 1, 1], False), ([1, 2, 1], False)]
)
def test_route_running_maximum_matches_frozen_input_order(
    routed, tmp_path, monkeypatch, versions, rejected
):
    route = routed["fulfillment_routes"][0]
    routed["fulfillment_routes"] = [{**route, "version": v} for v in versions]
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        if rejected:
            with pytest.raises(ValueError, match="ambiguous_stockout"):
                projected(routed, facts)
        else:
            projected(routed, facts)


def test_stock_change_never_resurrects_old_route_and_two_channels_pool_once(
    routed, tmp_path, monkeypatch
):
    add_channel(routed)
    routed["fulfillment_routes"].append(
        {**routed["fulfillment_routes"][0], "version": 2, "stock_location_id": "elsewhere"}
    )
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        point = projected(routed, facts)
        assert len(point.series) == 1 and point.series[0].channel == "online"
        assert point.forecast_units_7d == 26.0 * 7


@pytest.mark.parametrize(
    "table",
    [
        "daily_demand_versions",
        "fulfillment_routes",
        "business_calendar",
        "assortment",
        "price_plans",
    ],
)
def test_subsecond_late_revisions_keep_previous_forecast(routed, tmp_path, monkeypatch, table):
    old = upstream_point(routed, product="p-1", stock="stock", as_of=AS_OF)
    late = {
        **routed[table][0],
        "version": 99,
        "curated_available_at": make_origin(AS_OF.date()).forecast_origin
        + timedelta(microseconds=1),
    }
    if table == "daily_demand_versions":
        late["observed_units"] = 999999
    routed[table].append(late)
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        assert projected(routed, facts) == old


def test_known_target_calendar_and_unknown_day_match_frozen_projection(
    routed, tmp_path, monkeypatch
):
    day = AS_OF.date() + timedelta(days=1)
    routed["business_calendar"] = [
        r for r in routed["business_calendar"] if r["business_date"] != day
    ]
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        assert projected(routed, facts).reason == "target_calendar_unknown"


def test_foreign_old_business_date_tie_is_still_rejected(routed, tmp_path, monkeypatch):
    # Constructor validates latest versions before its history-date filter.
    old = {
        **routed["daily_demand_versions"][0],
        "business_date": AS_OF.date() - timedelta(days=500),
        "product_id": "foreign",
    }
    routed["daily_demand_versions"].extend([old, deepcopy(old)])
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        with pytest.raises(SnapshotError, match="ambiguous_forecast_known"):
            projected(routed, facts)


@pytest.mark.parametrize("mutation", ["payload", "index", "query_only"])
def test_private_database_change_blocks_global_cache_and_selection(native, tmp_path, mutation):
    _, curated, _, _ = native
    with SeriesFacts(curated.directory, scratch=tmp_path) as facts:
        at = datetime(2027, 1, 1, tzinfo=AS_OF.tzinfo)
        facts.validate_origin(at)
        db = facts._db
        db.execute("PRAGMA query_only=OFF")
        if mutation == "payload":
            db.execute(
                "UPDATE facts SET payload=? WHERE table_name='product_catalog'", (b"changed",)
            )
        elif mutation == "index":
            db.execute(
                "UPDATE series_index SET product='hidden' WHERE table_name='product_catalog'"
            )
        if mutation != "query_only":
            db.commit()
            db.execute("PRAGMA query_only=ON")
        with pytest.raises(SnapshotError, match="database_changed"):
            facts.known_series("any", "stock", at)


@pytest.mark.parametrize(
    "field,value", [("max_selected_rows", 1), ("max_selected_bytes", 1), ("max_db_bytes", 4096)]
)
def test_limits_block_publication_and_cleanup_scratch(series_bundle, tmp_path, field, value):
    curated, parent, _, _, _ = series_bundle
    with pytest.raises(SnapshotError, match="resource"):
        build_upstream_bundle(
            curated,
            parent,
            tmp_path / "output",
            storage_policy=UpstreamStoragePolicy.model_validate({field: value}),
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mutation", ["part", "missing", "extra", "resealed"])
def test_complete_output_validation_precedes_reader_first_yield(series_bundle, tmp_path, mutation):
    curated, parent, target, doc, _ = series_bundle
    copy = tmp_path / "copy"
    shutil.copytree(target, copy)
    part = copy / doc["descriptor"]["partitions"][-1]["path"]
    if mutation == "part":
        part.write_bytes(b"changed")
    elif mutation == "missing":
        part.unlink()
    elif mutation == "extra":
        (copy / "extra").write_bytes(b"x")
    else:
        body = deepcopy(doc)
        body["descriptor"]["selection_policy"] = "invented"
        body["upstream_bundle_id"] = (
            "upstream-partitions-sha256-"
            + hashlib.sha256(canonical_json(body["descriptor"])).hexdigest()
        )
        (copy / "manifest.json").write_bytes(canonical_json(body))
    with pytest.raises((SnapshotError, OSError)):
        next(iter_verified_upstream(copy, curated, parent))


@pytest.mark.parametrize("mutation", ["manifest", "part", "feature"])
def test_reader_rechecks_mutations_between_parts(series_bundle, tmp_path, mutation):
    curated, parent, target, doc, _ = series_bundle
    copy, features = tmp_path / "copy", tmp_path / "features"
    shutil.copytree(target, copy)
    shutil.copytree(parent, features)
    reader = iter_verified_upstream(copy, curated, features)
    for _ in range(doc["descriptor"]["partitions"][0]["rows"]):
        next(reader)
    if mutation == "manifest":
        (copy / "manifest.json").write_bytes(b"{}")
    elif mutation == "part":
        (copy / doc["descriptor"]["partitions"][1]["path"]).write_bytes(b"changed")
    else:
        ref = read_json(features, "manifest.json")["descriptor"]["partitions"][0]["files"][0]
        (features / ref["path"]).write_bytes(b"changed")
    with pytest.raises(SnapshotError):
        next(reader)
    reader.close()


def test_partition_and_bundle_limits_still_block_output(series_bundle, tmp_path, monkeypatch):
    curated, parent, _, _, _ = series_bundle
    with pytest.raises(SnapshotError, match="partition_byte"):
        build_upstream_bundle(
            curated,
            parent,
            tmp_path / "out",
            partition_policy=bundle.UpstreamPartitionPolicy(max_partition_bytes=1),
        )
    monkeypatch.setattr(bundle, "MAX_BUNDLE_BYTES", 1)
    with pytest.raises(SnapshotError, match="bundle_byte"):
        build_upstream_bundle(curated, parent, tmp_path / "out")
    assert list(tmp_path.iterdir()) == []


def test_known_correction_changes_estimate_without_using_future_actuals(
    routed, tmp_path, monkeypatch
):
    day = AS_OF.date() - timedelta(days=1)
    original = next(r for r in routed["daily_demand_versions"] if r["business_date"] == day)
    routed["daily_demand_versions"].append(
        {
            **original,
            "version": 2,
            "observed_units": 66,
            "curated_available_at": make_origin(AS_OF.date()).forecast_origin,
        }
    )
    for row in routed["daily_demand_versions"]:
        if row["business_date"] >= AS_OF.date():
            row["observed_units"] = 999999
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        assert projected(routed, facts).forecast_units_7d == 27.0 * 7


def test_lower_foreign_ties_superseded_by_known_higher_version_are_allowed(
    routed, tmp_path, monkeypatch
):
    original = {**routed["daily_demand_versions"][0], "product_id": "foreign"}
    routed["daily_demand_versions"].extend(
        [original, deepcopy(original), {**original, "version": 2}]
    )
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        projected(routed, facts)


def test_future_higher_version_cannot_hide_current_foreign_tie(routed, tmp_path, monkeypatch):
    original = {**routed["daily_demand_versions"][0], "product_id": "foreign"}
    routed["daily_demand_versions"].extend(
        [
            original,
            deepcopy(original),
            {**original, "version": 2, "curated_available_at": AS_OF + timedelta(days=1)},
        ]
    )
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        with pytest.raises(SnapshotError, match="ambiguous_forecast_known"):
            projected(routed, facts)


def test_unused_horizon14_projection_errors_are_preserved(routed, tmp_path, monkeypatch):
    # Frozen targets build days 1..14 before filtering the seven-day prediction.
    target = AS_OF.date() + timedelta(days=14)
    first = routed["price_plans"][0]
    routed["price_plans"].append(
        {
            **first,
            "plan_key": "overlap",
            "effective_from": target,
            "effective_to": target + timedelta(days=1),
        }
    )
    with pytest.raises(SnapshotError, match="ambiguous_forecast_price_scope"):
        upstream_point(routed, product="p-1", stock="stock", as_of=AS_OF)
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        with pytest.raises(SnapshotError, match="ambiguous_forecast_price_scope"):
            projected(routed, facts)


def test_parameterized_selection_does_not_expand_product_scope(routed, tmp_path, monkeypatch):
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        selected = facts.known_series(
            "x' OR 1=1 --", "stock", make_origin(AS_OF.date()).forecast_origin
        )
        assert selected["product_catalog"] == selected["daily_demand_versions"] == []
        with pytest.raises(ValidationError, match="product_id"):
            upstream_point(selected, product="x' OR 1=1 --", stock="stock", as_of=AS_OF)


def test_cached_rows_are_not_changed_by_caller_and_cache_is_cleared_on_close(
    routed, tmp_path, monkeypatch
):
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        selected = facts.known_series("p-1", "stock", make_origin(AS_OF.date()).forecast_origin)
        selected["product_catalog"][0]["category_id"] = "corrupt"
        selected["daily_demand_versions"][0]["observed_units"] = 999999
        assert projected(routed, facts).forecast_units_7d == 26.0 * 7
        assert facts.stats["decoded_cache_hits"] > 0
    assert not facts._decoded_cache and facts._decoded_cache_bytes == 0


def test_cached_later_revision_does_not_rewrite_an_earlier_origin(routed, tmp_path, monkeypatch):
    correction = {
        **routed["daily_demand_versions"][15],
        "version": 2,
        "observed_units": 9999,
        "curated_available_at": AS_OF + timedelta(microseconds=1),
    }
    routed["daily_demand_versions"].append(correction)
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        for offset in (1, 0, 1, 0):
            at = AS_OF + timedelta(days=offset)
            origin = make_origin(at.date()).forecast_origin
            selected = facts.known_series("p-1", "stock", origin)
            assert upstream_point(
                selected, product="p-1", stock="stock", as_of=at
            ) == upstream_point(routed, product="p-1", stock="stock", as_of=at)


@pytest.mark.parametrize("max_rows,max_bytes", [(8, 4096), (1, 1024), (0, 0)])
def test_decoded_cache_eviction_or_disable_preserves_predictions(
    routed, tmp_path, monkeypatch, max_rows, max_bytes
):
    monkeypatch.setattr(store, "MAX_CACHE_ROWS", max_rows)
    monkeypatch.setattr(store, "MAX_CACHE_BYTES", max_bytes)
    with synthetic(routed, tmp_path, monkeypatch) as facts:
        projected(routed, facts)
        projected(routed, facts)
        assert facts.stats["maximum_decoded_cache_rows"] <= max_rows
        assert facts.stats["maximum_decoded_cache_bytes"] <= max_bytes


def test_sealed_selection_does_not_reread_the_packaged_schema(routed, tmp_path, monkeypatch):
    with synthetic(routed, tmp_path, monkeypatch) as facts:

        def unexpected(*args):
            raise AssertionError("schema reread after sealing the series index")

        monkeypatch.setattr(store, "columns_for", unexpected)
        projected(routed, facts)
        projected(routed, facts)
