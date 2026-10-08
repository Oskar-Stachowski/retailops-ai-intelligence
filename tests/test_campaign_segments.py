"""Full critical inventories and causal context, with explicit controlled sources."""

import hashlib
import json
from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from test_stockout_features import records as records
from test_stockout_features import row as fact_row

from retailops_ai.data_contracts.common import ForecastKey, end_of_day
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignForecastContextScope,
    CampaignForecastSegmentCensus,
    CampaignForecastSegmentPolicy,
    CampaignOriginRoute,
    CampaignSourceKeyAnnotation,
    required_population_ids,
)
from retailops_ai.evaluation_campaign.campaign_segments import (
    SegmentCensus,
    context_from_inputs,
    population_ids,
)
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.forecasting.features_contract import (
    FEATURE_TYPES,
    HistoryContext,
    InputRow,
    InputValue,
    PanelPoint,
)
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout_history.projection import feature_point

ORIGIN = end_of_day(date(2026, 3, 28))


def policy(**updates):
    return CampaignForecastSegmentPolicy(category_inventory=("c1", "c2"), **updates)


def scope(p, **updates):
    return CampaignForecastContextScope(
        **(
            {
                "data_seed": 42,
                "role": "development_evaluation",
                "dataset_id": "ai09-physical-forecast-sha256-" + "a" * 64,
                "source_recipe_sha256": "a" * 64,
                "source_dataset_id": "source-sha256-" + "b" * 64,
                "curated_dataset_id": "curated-sha256-" + "c" * 64,
                "snapshot_id": "snapshot-sha256-" + "d" * 64,
                "source_scenario_plan_sha256": None,
                "segment_policy_sha256": p.content_sha256(),
            }
            | updates
        )
    )


def inputs(
    *,
    product="product",
    horizon=1,
    units=None,
    volume=2.0,
    late=False,
    missing_plan=False,
    promotion=False,
):
    units = [2] * 28 if units is None else units
    points = []
    for i, value in enumerate(units):
        day = ORIGIN.date() - timedelta(days=len(units) - 1 - i)
        status = (
            "missing"
            if value is None
            else "closed"
            if value == "closed"
            else "observed_positive"
            if value
            else "observed_zero"
        )
        available = (
            None
            if value is None
            else min(ORIGIN, end_of_day(day) + timedelta(hours=48 if late else 0))
        )
        points.append(
            PanelPoint(
                product_id=product,
                selling_location_id="shop",
                channel="store",
                forecast_origin=ORIGIN,
                business_date=day,
                status=status,
                observed_units=None if value is None else 0 if value == "closed" else value,
                source_data_complete=value is not None,
                location_open=None if value is None else value != "closed",
                source_available_at=available,
                references=(),
            )
        )
    history = HistoryContext(
        product_id=product,
        selling_location_id="shop",
        channel="store",
        forecast_origin=ORIGIN,
        points=tuple(points),
    )
    target = ORIGIN.date() + timedelta(days=horizon)
    special = {
        "category_id": "c1",
        "brand": "brand",
        "country_code": "PL",
        "calendar_jurisdiction": "PL",
        "channel": "store",
        "currency": "PLN",
        "planned_promotion_type": "none",
        "planned_promotion_offered": promotion,
        "target_location_open": True,
        "target_weekday": target.weekday(),
        "target_week_of_year": target.isocalendar().week,
        "target_month": target.month,
        "target_quarter": (target.month - 1) // 3 + 1,
        "target_is_weekend": target.weekday() >= 5,
        "rolling_mean_28": volume,
    }
    values = []
    for name, type_name in FEATURE_TYPES.items():
        kind = (
            "observed"
            if name.startswith(("origin_lag_", "rolling_"))
            else "calendar"
            if name.startswith("target_")
            else "categorical"
            if name in {"category_id", "brand", "country_code", "calendar_jurisdiction", "channel"}
            else "known_plan"
        )
        value = special.get(name, {"int": 2, "float": 2.0, "bool": False, "str": "none"}[type_name])
        missing = value is None or missing_plan and name == "planned_regular_price_minor_units"
        through = (
            ORIGIN.date() + timedelta(days=1 - int(name.split("_")[2]))
            if name.startswith("origin_lag_")
            else ORIGIN.date()
        )
        values.append(
            InputValue(
                name=name,
                kind=kind,
                value=None if missing else value,
                status="missing" if missing else "available",
                source_available_at=None if missing else ORIGIN,
                reason="controlled_missing" if missing else None,
                observed_through_date=through if kind == "observed" else None,
                effective_date=None if kind == "observed" else target,
            )
        )
    known = sum(v is not None for v in units)
    feature = InputRow(
        product_id=product,
        selling_location_id="shop",
        channel="store",
        forecast_origin=ORIGIN,
        business_timezone="UTC",
        cutoff_policy="end_of_day_second_v1",
        target_date=target,
        horizon_days=horizon,
        history_context_sha256=history.content_sha256(),
        history_active_days=len(units),
        history_known_days=known,
        history_missing_days=len(units) - known,
        history_closed_days=units.count("closed"),
        insufficient_history=len(units) < 28 or known < 7,
        target_calendar_eligible=True,
        values=tuple(values),
    )
    return feature, history


def annotation(feature, s, *, scenario="normal", anomaly="unannotated"):
    return CampaignSourceKeyAnnotation(
        **feature.model_dump(include=set(ForecastKey.model_fields)),
        context_scope_sha256=s.content_sha256(),
        source_scenario_plan_sha256=s.source_scenario_plan_sha256,
        scenario=scenario,
        anomaly=anomaly,
    )


def route():
    return CampaignOriginRoute(
        product_id="product",
        selling_location_id="shop",
        channel="store",
        stock_location_id="stock",
        source_record_sha256="f" * 64,
        available_at=ORIGIN - timedelta(days=100),
        effective_from=date(2026, 1, 1),
        effective_to=date(2027, 1, 1),
    )


def context(
    feature,
    history,
    *,
    p=None,
    s=None,
    inventory=None,
    active_route=None,
    active_annotation=None,
    excluded=False,
):
    p = p or policy()
    s = s or scope(p)
    return context_from_inputs(
        feature,
        history,
        inventory,
        active_route,
        active_annotation or annotation(feature, s),
        scope=s,
        policy=p,
        example_sha256="e" * 64,
        eligible=not excluded,
        exclusion_reasons=("insufficient_history",) if excluded else (),
    )


def expected(rows):
    keys, eligible = hashlib.sha256(), hashlib.sha256()
    for r in sorted(rows, key=membership_key):
        raw = membership_key(r) + b"\n"
        keys.update(raw)
        if r.eligible:
            eligible.update(raw)
    return {
        "expected_rows": len(rows),
        "expected_eligible_rows": sum(r.eligible for r in rows),
        "expected_keys_sha256": keys.hexdigest(),
        "expected_eligible_keys_sha256": eligible.hexdigest(),
    }


def census(rows, p=None, s=None):
    p = p or policy()
    index = SegmentCensus(s or scope(p), p)
    for r in sorted(rows, key=membership_key):
        index.add(r)
    return index.finish(**expected(rows))


def test_complete_inventory_keeps_empty_required_categories_scenarios_and_anomaly_types():
    feature, history = inputs()
    result = census([context(feature, history)])
    assert len(result.populations) == 57
    groups = {(p.dimension, p.value): p for p in result.populations}
    assert tuple(groups) == required_population_ids(result.policy)
    assert groups[("category", "c1")].rows == 1 and groups[("category", "c2")].rows == 0
    assert groups[("scenario", "demand_shock")].rows == 0
    assert groups[("anomaly", "return_spike")].rows == 0
    assert all(
        sum(g.rows for (d, _), g in groups.items() if d == dimension) == 1
        for dimension in {d for d, _ in groups} - {"availability"}
    )
    assert not result.audited_source_read_proved_by_this_document
    assert (
        not result.metric_gates_evaluated
        and not result.quality_qualified
        and not result.stage_ready
    )


@pytest.mark.parametrize(
    "mean,bin_name",
    [
        (0.0, "zero"),
        (4.999, "low"),
        (5.0, "medium"),
        (19.999, "medium"),
        (20.0, "high"),
        (None, "unknown"),
    ],
)
def test_volume_is_origin_known_feature_with_frozen_boundaries(mean, bin_name):
    feature, history = inputs(volume=mean)
    value = context(feature, history)
    assert ("volume", bin_name) in population_ids(value)
    assert "actual" not in type(value).model_fields
    assert "target_sales_units" not in type(value).model_fields


@pytest.mark.parametrize(
    "units,expected_state",
    [
        ([0] * 28, "all_zero_known_open_history"),
        ([0, 2] * 14, "intermittent_known_open_history"),
        ([2] * 28, "positive_on_all_known_open_days"),
        (["closed"] * 28, "no_known_open_days"),
        ([None] + [0] * 27, "incomplete_history"),
    ],
)
def test_closed_and_missing_days_do_not_create_an_all_zero_sales_series(units, expected_state):
    feature, history = inputs(units=units)
    assert context(feature, history).intermittency == expected_state


def test_availability_flags_overlap_and_cold_start_stays_excluded_not_removed():
    feature, history = inputs(units=[None, 2, 2, 2, 2], late=True, missing_plan=True, volume=None)
    value = context(feature, history, excluded=True)
    assert value.history_state == "cold_start"
    assert value.availability == (
        "late_history",
        "missing_history",
        "missing_known_plan",
        "other_missing_input",
    )
    result = census([value])
    availability = [p for p in result.populations if p.dimension == "availability"]
    assert sum(p.rows for p in availability) == 4 and result.rows == 1 and result.eligible_rows == 0
    assert (
        next(
            p for p in result.populations if (p.dimension, p.value) == ("history", "cold_start")
        ).rows
        == 1
    )


@pytest.mark.parametrize(
    "quantity,age,quote,state,bin_name",
    [
        (100, 0.0, 2.0, "known_positive", "le_2_days"),
        (0, 0.0, 2.1, "known_zero", "gt_2_le_7_days"),
        (100, 24.0, 7.0, "known_positive", "gt_2_le_7_days"),
        (100, 24.1, 7.1, "stale", "gt_7_days"),
        (None, None, None, "unknown", "unknown"),
    ],
)
def test_inventory_and_quotes_reuse_real_ai08_projection_with_explicit_boundary_cases(
    records,
    quantity,
    age,
    quote,
    state,
    bin_name,
):
    records["inventory_daily_snapshots"] = [
        fact_row(
            available=ORIGIN,
            product_id="product",
            stock_location_id="stock",
            snapshot_at=ORIGIN,
            available_qty=100,
            on_hand=100,
            reserved_qty=0,
            status="known",
        )
    ]
    point = feature_point(records, product="product", stock="stock", as_of=ORIGIN)
    # Deliberate typed metadata boundaries after a real controlled projection.
    # This is not a qualified source artifact or a project result.
    values = point.values.model_copy(
        update={
            "available_qty": quantity,
            "snapshot_age_hours": age,
            "quoted_lead_time_days": quote,
        }
    )
    point = point.model_copy(
        update={
            "values": values,
            "status": "already_stockout"
            if quantity == 0
            else "insufficient_data"
            if quantity is None
            else "eligible",
            "reason": "inventory_unknown" if quantity is None else None,
        }
    )
    feature, history = inputs()
    value = context(feature, history, inventory=point, active_route=route())
    assert value.inventory_state == state and ("lead_time", bin_name) in population_ids(value)
    assert value.origin_inventory_feature_sha256 == canonical_sha256(point.model_dump(mode="json"))
    assert value.origin_available_qty == quantity and value.origin_quoted_lead_time_days == quote


def test_future_inventory_snapshot_never_becomes_origin_context_and_future_quote_is_clipped(
    records,
):
    feature, history = inputs()
    records["product_suppliers"][0]["curated_available_at"] = ORIGIN + timedelta(seconds=1)
    point = feature_point(records, product="product", stock="stock", as_of=ORIGIN)
    # The unmodified fixture snapshot is 999999 microseconds after this origin.
    value = context(feature, history, inventory=point, active_route=route())
    assert value.inventory_state == "unknown" and value.origin_available_qty is None
    assert value.origin_quoted_lead_time_days is None


@pytest.mark.parametrize(
    "mutation",
    [
        "scope",
        "history",
        "history_counts",
        "annotation_key",
        "route_future",
        "route_key",
        "route_interval",
        "inventory_without_route",
    ],
)
def test_context_rejects_mismatched_sources_keys_histories_or_future_routes(records, mutation):
    feature, history = inputs()
    p = policy()
    s = scope(p)
    a = annotation(feature, s)
    r = None
    inventory = None
    if mutation == "scope":
        s = s.model_copy(update={"segment_policy_sha256": "f" * 64})
    elif mutation == "history":
        history = history.model_copy(update={"product_id": "other"})
    elif mutation == "history_counts":
        feature = feature.model_copy(
            update={
                "history_active_days": 27,
                "history_known_days": 27,
                "insufficient_history": True,
            }
        )
    elif mutation == "annotation_key":
        a = a.model_copy(update={"product_id": "other"})
    elif mutation == "route_future":
        r = route().model_copy(update={"available_at": ORIGIN + timedelta(seconds=1)})
    elif mutation == "route_key":
        r = route().model_copy(update={"product_id": "other"})
    elif mutation == "route_interval":
        r = route().model_copy(update={"effective_from": ORIGIN.date() + timedelta(days=1)})
    elif mutation == "inventory_without_route":
        inventory = feature_point(records, product="product", stock="stock", as_of=ORIGIN)
    with pytest.raises((SnapshotError, ValidationError)):
        context(
            feature, history, p=p, s=s, active_annotation=a, active_route=r, inventory=inventory
        )


@pytest.mark.parametrize("mutation", ["duplicate", "scope", "category", "row_budget"])
def test_census_failure_latch_rejects_duplicate_scope_and_easy_category_subset(mutation):
    p = policy(**({"max_rows": 1} if mutation == "row_budget" else {}))
    s = scope(p)
    f, h = inputs()
    first = context(f, h, p=p, s=s)
    index = SegmentCensus(s, p)
    index.add(first)
    f, h = inputs(product="product2")
    second = context(f, h, p=p, s=s)
    if mutation == "duplicate":
        second = first
    elif mutation == "scope":
        second = second.model_copy(
            update={
                "context_scope_sha256": "f" * 64,
                "annotation": second.annotation.model_copy(
                    update={"context_scope_sha256": "f" * 64}
                ),
            }
        )
    elif mutation == "category":
        second = second.model_copy(update={"category": "undeclared"})
    with pytest.raises(SnapshotError):
        index.add(second)
    with pytest.raises(SnapshotError, match="stream_unavailable"):
        index.finish(**expected([first, second]))


def test_truncated_census_cannot_claim_complete_key_membership():
    f, h = inputs()
    first = context(f, h)
    f, h = inputs(product="product2")
    second = context(f, h)
    index = SegmentCensus(scope(policy()), policy())
    index.add(first)
    with pytest.raises(SnapshotError, match="complete_membership_mismatch"):
        index.finish(**expected([first, second]))


def test_census_schema_rejects_dropped_empty_groups_unbalanced_partitions_and_permission_flags():
    f, h = inputs()
    report = census([context(f, h)])
    original = report.model_dump(mode="json")
    for mutation in ("drop_empty", "unbalance", "final_access"):
        value = json.loads(json.dumps(original))
        if mutation == "drop_empty":
            value["populations"] = [p for p in value["populations"] if p["rows"]]
        elif mutation == "unbalance":
            next(p for p in value["populations"] if p["dimension"] == "volume" and p["rows"])[
                "rows"
            ] = 0
            next(
                p for p in value["populations"] if p["dimension"] == "volume" and p["eligible_rows"]
            )["eligible_rows"] = 0
        else:
            value["final_access_authorized"] = True
        with pytest.raises(ValidationError):
            CampaignForecastSegmentCensus.model_validate_json(json.dumps(value))


def test_annotation_cannot_invent_effect_without_frozen_plan_or_known_promotion():
    f, h = inputs()
    p = policy()
    s = scope(p)
    with pytest.raises(ValidationError, match="has_no_source_plan"):
        annotation(f, s, scenario="demand_shock", anomaly="one_day_spike")
    s = scope(p, source_scenario_plan_sha256="f" * 64)
    with pytest.raises(SnapshotError, match="promotion_annotation_without_known_offer"):
        context(f, h, p=p, s=s, active_annotation=annotation(f, s, scenario="promotion"))
    f, h = inputs(promotion=True)
    value = context(
        f,
        h,
        p=p,
        s=s,
        active_annotation=annotation(f, s, scenario="promotion", anomaly="clean_control"),
    )
    assert value.annotation.scenario == "promotion" and value.annotation.anomaly == "clean_control"
    assert not value.annotation.effect_independence_claimed and not value.final_access_authorized


@pytest.mark.parametrize("mutation", ["missing_availability", "overlapping_complete", "reason"])
def test_census_wire_rejects_impossible_availability_or_changed_partition_exclusions(mutation):
    f, h = inputs(units=[2] * 5)
    value = census([context(f, h, excluded=True)]).model_dump(mode="json")
    if mutation == "missing_availability":
        next(
            p
            for p in value["populations"]
            if (p["dimension"], p["value"]) == ("availability", "complete")
        ).update(rows=0, exclusion_reasons={})
    elif mutation == "overlapping_complete":
        next(
            p
            for p in value["populations"]
            if (p["dimension"], p["value"]) == ("availability", "missing_history")
        ).update(rows=1, exclusion_reasons={"insufficient_history": 1})
    else:
        next(
            p
            for p in value["populations"]
            if (p["dimension"], p["value"]) == ("history", "cold_start")
        )["exclusion_reasons"] = {}
    with pytest.raises(ValidationError):
        CampaignForecastSegmentCensus.model_validate_json(json.dumps(value))
