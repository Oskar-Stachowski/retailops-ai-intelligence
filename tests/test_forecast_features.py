"""Calendar-day features, frozen history, lifecycle, known pricing and physical draft inputs."""

import hashlib
import json
import shutil
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from retailops_ai.curated.builder import build_curated
from retailops_ai.curated.contract import encoded
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.calendar import build_calendar, publish_calendar
from retailops_ai.forecasting.cli import main
from retailops_ai.forecasting.contract import OriginWindow, make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_contract import FEATURE_TYPES, TABLES, InputRow
from retailops_ai.forecasting.features_store import (
    KEYS,
    build_inputs,
    physical_row,
    source_index,
    verify_inputs,
)
from retailops_ai.forecasting.features_store import schema as arrow_schema
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.source_snapshot.importer import import_snapshot

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 6, 1)
ORIGIN = make_origin(DAY)
SERIES = ("p-1", "s-1", "store")
KNOWN = datetime(2026, 1, 1, tzinfo=UTC)


def fact(**row):
    available = row.pop("curated_available_at", KNOWN)
    return {
        **row,
        "curated_available_at": available,
        "source_record_sha256": hashlib.sha256(encoded(row)).hexdigest(),
    }


@pytest.fixture
def tables():
    start, end = DAY - timedelta(days=39), DAY + timedelta(days=15)
    result = {name: [] for name in TABLES}
    result["product_catalog"] = [
        fact(
            id="p-1",
            category_id="cat-1",
            brand="Brand A",
            launch_date=start,
            discontinue_date=None,
            currency="PLN",
        )
    ]
    interval = {
        "version": 1,
        "selling_location_id": "s-1",
        "channel": "store",
        "effective_from": start,
        "effective_to": end,
    }
    result["channel_assignments"] = [fact(id="assignment", assignment_key="pair", **interval)]
    result["assortment"] = [
        fact(id="assortment", assortment_key="series", product_id="p-1", **interval)
    ]
    result["price_plans"] = [
        fact(
            id="price",
            plan_key="regular",
            version=1,
            product_id="p-1",
            scope="global",
            selling_location_id=None,
            channel="all",
            effective_from=start,
            effective_to=end,
            price=Decimal("12.34"),
            currency="PLN",
            pricing_policy_version="retail-pricing-1.0.0",
        )
    ]
    for i in range(54):
        day = start + timedelta(days=i)
        result["business_calendar"].append(
            fact(
                id=f"calendar-{i}",
                business_date=day,
                selling_location_id="s-1",
                channel="store",
                location_open=True,
                country_code="PL",
                calendar_jurisdiction="PL",
                is_public_holiday=False,
                is_easter=False,
                is_christmas=False,
                is_black_friday=False,
                is_cyber_monday=False,
            )
        )
        result["category_calendar"].append(
            fact(
                id=f"season-{i}",
                business_date=day,
                category_id="cat-1",
                is_category_season=i % 2 == 0,
            )
        )
        result["daily_demand_versions"].append(
            fact(
                id=f"obs-{i}",
                business_date=day,
                product_id="p-1",
                selling_location_id="s-1",
                channel="store",
                version=1,
                observed_units=i + 1,
                observation_status="observed_positive",
                curated_available_at=datetime.combine(
                    day + timedelta(days=1), datetime.min.time(), UTC
                ),
            )
        )
    return result


def rows(tables, origin=ORIGIN):
    view = OriginFeatures(tables, origin)
    history = view.history(SERIES)
    return history, view.targets(history)


def values(row):
    return {v.name: v for v in row.values}


def test_true_calendar_lags_same_across_all_fourteen_horizons(tables):
    history, output = rows(tables)
    assert len(history.points) == 28 and len(output) == 14
    first = values(output[0])
    assert first["origin_lag_1_units"].value is None
    assert first["origin_lag_7_units"].value == 34
    assert first["origin_lag_14_units"].value == 27
    assert first["origin_lag_28_units"].value == 13
    observed = [v for v in output[0].values if v.kind == "observed"]
    assert all([v for v in r.values if v.kind == "observed"] == observed for r in output)
    assert len({r.history_context_sha256 for r in output}) == 1
    assert all(
        r.history_known_days == 27 and r.history_missing_days == 1 and not r.insufficient_history
        for r in output
    )
    assert first["rolling_count_7"].value == 6
    assert first["rolling_mean_7"].value == 36.5
    assert first["rolling_std_7"].value == pytest.approx(1.70782512766)
    assert first["planned_regular_price_minor_units"].value == 1234


def test_missing_day_does_not_shift_lag_seven_or_turn_into_zero(tables):
    missing_day = DAY - timedelta(days=6)
    tables["daily_demand_versions"] = [
        r for r in tables["daily_demand_versions"] if r["business_date"] != missing_day
    ]
    history, output = rows(tables)
    missing = next(p for p in history.points if p.business_date == missing_day)
    assert (
        missing.status == "missing"
        and missing.observed_units is None
        and not missing.source_data_complete
    )
    assert values(output[0])["origin_lag_7_units"].value is None
    assert values(output[0])["rolling_count_7"].value == 5
    assert values(output[0])["rolling_mean_7"].value == 37.0


def test_microsecond_availability_and_incomplete_observation_are_not_rounded(tables):
    today = next(r for r in tables["daily_demand_versions"] if r["business_date"] == DAY)
    today["curated_available_at"] = ORIGIN.forecast_origin
    assert values(rows(tables)[1][0])["origin_lag_1_units"].value == 40
    today["curated_available_at"] += timedelta(microseconds=1)
    assert values(rows(tables)[1][0])["origin_lag_1_units"].value is None
    today["curated_available_at"] = ORIGIN.forecast_origin
    today["source_data_complete"] = False
    assert values(rows(tables)[1][0])["origin_lag_1_units"].value is None


def test_empty_history_and_single_known_observation_keep_counts_and_cold_start(tables):
    saved = tables["daily_demand_versions"]
    tables["daily_demand_versions"] = []
    history, output = rows(tables)
    assert len(history.points) == 28 and output[0].insufficient_history
    vals = values(output[0])
    assert vals["rolling_count_7"].value == 0
    assert vals["rolling_mean_7"].value is None and vals["rolling_std_7"].value is None
    tables["daily_demand_versions"] = [
        r for r in saved if r["business_date"] == DAY - timedelta(days=1)
    ]
    vals = values(rows(tables)[1][0])
    assert vals["rolling_count_7"].value == 1 and vals["rolling_std_7"].value == 0.0


def test_confirmed_zero_closed_and_missing_remain_distinct(tables):
    zero_day, closed_day = DAY - timedelta(days=2), DAY - timedelta(days=7)
    for row in tables["daily_demand_versions"]:
        if row["business_date"] in (zero_day, closed_day):
            row.update(
                observed_units=0,
                observation_status="closed"
                if row["business_date"] == closed_day
                else "observed_zero",
            )
    for row in tables["business_calendar"]:
        if row["business_date"] == closed_day or row["business_date"] == DAY + timedelta(days=1):
            row["location_open"] = False
    history, output = rows(tables)
    indexed = {p.business_date: p for p in history.points}
    assert indexed[zero_day].status == "observed_zero" and indexed[zero_day].observed_units == 0
    assert indexed[closed_day].status == "closed" and indexed[closed_day].observed_units == 0
    assert indexed[DAY].status == "missing" and indexed[DAY].observed_units is None
    assert len(output) == 14 and not output[0].target_calendar_eligible
    assert output[0].history_closed_days == 1


def test_only_active_lifecycle_assignment_and_assortment_dates_are_reindexed(tables):
    tables["product_catalog"][0].update(
        launch_date=DAY - timedelta(days=2), discontinue_date=DAY + timedelta(days=3)
    )
    history, output = rows(tables)
    assert len(history.points) == 3 and all(
        p.business_date >= DAY - timedelta(days=2) for p in history.points
    )
    assert [r.horizon_days for r in output] == [1, 2]
    assert all(r.insufficient_history for r in output)
    tables["channel_assignments"][0]["effective_to"] = DAY + timedelta(days=2)
    assert [r.horizon_days for r in rows(tables)[1]] == [1]


def test_future_sales_price_promotion_and_correction_do_not_change_old_inputs(tables):
    history, output = rows(tables)
    later = ORIGIN.forecast_origin + timedelta(microseconds=1)
    observation = next(
        r for r in tables["daily_demand_versions"] if r["business_date"] == DAY - timedelta(days=13)
    )
    tables["daily_demand_versions"].append(
        fact(
            **{
                k: v
                for k, v in observation.items()
                if k not in {"source_record_sha256", "curated_available_at"}
            },
            curated_available_at=later,
        )
    )
    tables["daily_demand_versions"][-1].update(version=2, observed_units=999)
    override = deepcopy(tables["price_plans"][0])
    override.update(version=2, curated_available_at=later, price=Decimal("99.99"))
    tables["price_plans"].append(override)
    tables["promotion_plans"].append(
        fact(
            id="late-promo",
            promotion_key="late",
            version=1,
            product_id="p-1",
            scope="global",
            selling_location_id=None,
            channel="all",
            effective_from=DAY,
            effective_to=DAY + timedelta(days=15),
            curated_available_at=later,
            promotion_type="bundle",
            discount_percent=Decimal("15.00"),
            minimum_quantity=2,
            priority=100,
            status="active",
            stacking_policy="exclusive_highest_priority",
            pricing_policy_version="retail-pricing-1.0.0",
        )
    )
    assert rows(tables) == (history, output)
    _, later_rows = rows(tables, make_origin(DAY + timedelta(days=1)))
    assert values(later_rows[0])["planned_regular_price_minor_units"].value == 9999
    assert values(later_rows[0])["planned_promotion_offered"].value is True
    assert (
        values(later_rows[0])["rolling_mean_28"].value != values(output[0])["rolling_mean_28"].value
    )


def test_price_scope_known_override_and_promotion_offer_do_not_use_future_quantity(tables):
    price = deepcopy(tables["price_plans"][0])
    price.update(
        id="scoped",
        plan_key="scoped",
        scope="location_channel",
        selling_location_id="s-1",
        channel="store",
        price=Decimal("10.00"),
    )
    tables["price_plans"].append(price)
    promotion = fact(
        id="promo",
        promotion_key="campaign",
        version=1,
        product_id="p-1",
        scope="global",
        selling_location_id=None,
        channel="all",
        effective_from=DAY,
        effective_to=DAY + timedelta(days=15),
        promotion_type="bundle",
        discount_percent=Decimal("15.00"),
        minimum_quantity=2,
        priority=100,
        status="active",
        stacking_policy="exclusive_highest_priority",
        pricing_policy_version="retail-pricing-1.0.0",
    )
    tables["promotion_plans"] = [promotion]
    first = values(rows(tables)[1][0])
    assert first["planned_regular_price_minor_units"].value == 1000
    assert first["planned_promotion_offered"].value is True
    assert first["planned_promotion_minimum_quantity"].value == 2
    assert first["planned_promotion_discount_basis_points"].value == 1500
    cancelled = deepcopy(promotion)
    cancelled.update(version=2, status="cancelled")
    tables["promotion_plans"].append(cancelled)
    first = values(rows(tables)[1][0])
    assert first["planned_promotion_offered"].value is False
    assert first["planned_promotion_type"].status == "not_applicable"


@pytest.mark.parametrize(
    "field", ["inventory", "simulation_truth", "daily_price_observations", "forecasts", "sales"]
)
def test_unapproved_input_namespaces_are_rejected(tables, field):
    tables[field] = []
    with pytest.raises(SnapshotError, match="not_allowlisted"):
        rows(tables)


@pytest.mark.parametrize(
    "table", ["price_plans", "assortment", "daily_demand_versions", "promotion_plans"]
)
def test_ambiguous_known_versions_scope_or_priority_block_build(tables, table):
    if table == "promotion_plans":
        promo = fact(
            id="promo",
            promotion_key="campaign",
            version=1,
            product_id="p-1",
            scope="global",
            selling_location_id=None,
            channel="all",
            effective_from=DAY,
            effective_to=DAY + timedelta(days=15),
            promotion_type="percentage",
            discount_percent=Decimal("10.00"),
            minimum_quantity=1,
            priority=100,
            status="active",
            stacking_policy="exclusive_highest_priority",
            pricing_policy_version="retail-pricing-1.0.0",
        )
        tables[table] = [promo]
    tables[table].append(deepcopy(tables[table][0]))
    with pytest.raises(SnapshotError, match="ambiguous"):
        rows(tables)


def test_missing_plans_and_calendars_are_explicit_and_never_use_actual_price(tables):
    tables["price_plans"] = []
    tables["business_calendar"] = []
    tables["category_calendar"] = []
    history, output = rows(tables)
    first = values(output[0])
    assert first["planned_regular_price_minor_units"].status == "missing"
    assert first["target_is_public_holiday"].value is None
    assert first["target_is_category_season"].value is None
    assert first["target_weekday"].value == (DAY + timedelta(days=1)).weekday()
    assert not output[0].target_calendar_eligible
    assert history.points


@pytest.mark.parametrize(
    "mutation",
    [
        "feature_name",
        "availability",
        "future_history_date",
        "wrong_lag_date",
        "cold_start",
        "closed_target",
        "unknown_field",
        "wrong_calendar",
        "wrong_channel",
    ],
)
def test_feature_row_rejects_leakage_and_inconsistent_metadata(tables, mutation):
    payload = rows(tables)[1][0].model_dump(mode="json")
    if mutation == "feature_name":
        payload["values"][0]["name"] = "latent_demand"
    elif mutation == "availability":
        payload["values"][1]["source_available_at"] = "2026-06-02T00:00:00Z"
    elif mutation in {"future_history_date", "wrong_lag_date"}:
        payload["values"][1]["observed_through_date"] = (
            "2026-06-02" if mutation == "future_history_date" else "2026-05-25"
        )
    elif mutation == "cold_start":
        payload["insufficient_history"] = True
    elif mutation == "closed_target":
        payload["target_calendar_eligible"] = False
    elif mutation == "wrong_calendar":
        next(v for v in payload["values"] if v["name"] == "target_month")["value"] = 12
    elif mutation == "wrong_channel":
        next(v for v in payload["values"] if v["name"] == "channel")["value"] = "online"
    else:
        payload["actual_target_price"] = 3.0
    with pytest.raises(ValidationError):
        InputRow.model_validate_json(json.dumps(payload))


def test_big_money_precision_and_schema(tables):
    tables["price_plans"][0]["price"] = Decimal("123456789012345678901234567890123456.78")
    row = rows(tables)[1][0]
    assert (
        values(row)["planned_regular_price_minor_units"].value
        == 12345678901234567890123456789012345678
    )
    schema = json.loads((ROOT / "contracts/forecast/v1/input_row.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(row.model_dump(mode="json"))


@pytest.fixture(scope="module")
def real_inputs(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("forecast-inputs").resolve()
    imported = import_snapshot(
        ROOT / "data/fixtures/ai-smoke-v1/snapshot", workspace / "data/generated"
    )
    curated = build_curated(imported.directory, workspace / "data/generated")
    calendar = build_calendar(
        curated.directory, OriginWindow(start=date(2026, 7, 16), end=date(2026, 7, 17))
    )
    directory = build_inputs(curated.directory, calendar, workspace / "draft-inputs")
    return curated, calendar, directory


def test_real_typed_pipeline_and_repeated_publication_are_immutable(real_inputs):
    curated, calendar, directory = real_inputs
    before = {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*")
        if p.is_file()
    }
    manifest = verify_inputs(directory)
    assert manifest["descriptor"]["stats"]["active_target_rows"] > 0
    assert manifest["descriptor"]["stats"]["history_missing"] > 0
    assert manifest["forecast_model_status"] == "not_ready"
    assert build_inputs(curated.directory, calendar, directory.parent) == directory
    assert before == {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*")
        if p.is_file()
    }
    assert not list(directory.parent.glob(".forecast-inputs-*"))
    with source_index(curated.directory, calendar) as index:
        assert set(index.origin_tables(calendar.origins[0])) == set(TABLES)
    features = pq.read_table(directory / manifest["tables"]["features"]["files"][0]["path"])
    assert set(FEATURE_TYPES).issubset(features.column_names)
    assert "inventory" not in features.column_names
    assert len(set(features.column_names)) == len(features.column_names)


def test_corrupted_inputs_extra_files_and_cli_error_are_rejected(real_inputs, tmp_path, capsys):
    curated, calendar, original = real_inputs
    destination = tmp_path / "corrupted"
    shutil.copytree(original, destination)
    (destination / "unexpected.txt").write_text("extra")
    with pytest.raises(SnapshotError):
        verify_inputs(destination)
    (destination / "unexpected.txt").unlink()
    manifest = verify_inputs(destination)
    path = destination / manifest["tables"]["features"]["files"][0]["path"]
    path.write_bytes(path.read_bytes() + b"changed")
    assert main(["inputs-verify", "--inputs-dir", str(destination)]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and str(destination) not in captured.err
    assert json.loads(captured.err)["error"] == "forecast_rejected"
    assert main(["inputs-verify", "--inputs-dir", str(original)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"
    calendar_path = publish_calendar(calendar, tmp_path / "calendars")
    assert (
        main(
            [
                "inputs-build",
                "--curated-dir",
                str(curated.directory),
                "--calendar",
                str(calendar_path),
                "--output-root",
                str(original.parent),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["inputs_id"] == manifest["inputs_id"]


def test_rehashed_forged_statistic_is_rejected_by_history_binding(real_inputs, tmp_path):
    _, _, original = real_inputs
    directory = tmp_path / "forged"
    shutil.copytree(original, directory)
    manifest_path = directory / "inputs_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    ref = manifest["tables"]["features"]["files"][0]
    path = directory / ref["path"]
    table = pq.read_table(path)
    payloads = table.to_pylist()
    data = json.loads(payloads[0]["body_json"])
    next(v for v in data["values"] if v["name"] == "rolling_mean_7")["value"] = 123456.0
    payloads[0] = physical_row(InputRow.model_validate_json(json.dumps(data)))
    pq.write_table(
        pa.Table.from_pylist(payloads, schema=arrow_schema("features")), path, compression="zstd"
    )
    ref.update(size_bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    logical = []
    for file_ref in manifest["tables"]["features"]["files"]:
        for item in pq.read_table(directory / file_ref["path"]).to_pylist():
            row = InputRow.model_validate_json(item["body_json"]).model_dump(mode="json")
            logical.append(
                (canonical_bytes([row[k] for k in (*KEYS, "target_date")]), canonical_bytes(row))
            )
    content_hash = hashlib.sha256()
    for _, body in sorted(logical):
        content_hash.update(body + b"\n")
    manifest["tables"]["features"]["content_sha256"] = content_hash.hexdigest()
    manifest["descriptor"]["tables"]["features"]["content_sha256"] = content_hash.hexdigest()
    manifest["inputs_id"] = "forecast-inputs-sha256-" + canonical_sha256(manifest["descriptor"])
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SnapshotError, match="history_or_statistics_mismatch"):
        verify_inputs(directory)
