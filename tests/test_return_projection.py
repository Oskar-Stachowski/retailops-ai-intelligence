"""Counterexamples for event-day returns, causal availability and unknown coverage."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from retailops_ai.return_inputs.contract import Point, Policy
from retailops_ai.return_inputs.projection import Returns
from retailops_ai.source_snapshot.files import SnapshotError

SOLD = datetime(2026, 7, 31, 9, tzinfo=UTC)
RETURNED = SOLD + timedelta(days=2)
AVAILABLE = RETURNED + timedelta(days=2)
SERIES = ("product", "shop", "online", "PLN")


def policy(origin=AVAILABLE):
    return Policy(start_date=date(2026, 8, 1), end_date=date(2026, 8, 2), as_of_time=origin)


@pytest.fixture
def tables():
    common = dict(
        zip(("product_id", "selling_location_id", "channel", "currency"), SERIES, strict=True)
    )
    return {
        "inventory_sales": [
            {
                **common,
                "sale_id": "sale",
                "order_id": "order",
                "quantity": 4,
                "unit_price": Decimal("3.17"),
                "ordered_at": SOLD - timedelta(minutes=1),
                "sold_at": SOLD,
                "curated_available_at": SOLD + timedelta(minutes=2),
                "stock_location_id": "original-stock",
            }
        ],
        "sale_price_references": [
            {
                **common,
                "sale_id": "sale",
                "order_item_id": "item",
                "curated_available_at": SOLD + timedelta(minutes=2),
            }
        ],
        "product_catalog": [
            {
                "id": "product",
                "category_id": "category",
                "currency": "PLN",
                "curated_available_at": SOLD - timedelta(days=1),
                "discontinue_date": date(2026, 8, 1),
            }
        ],
        "return_policies": [
            {
                "id": "rule",
                "channel": "online",
                "category_id": "category",
                "known_at": SOLD - timedelta(days=1),
                "curated_available_at": SOLD - timedelta(days=1),
                "returns_policy_version": "retail-returns-1.0.0",
                "window_days": 30,
                "max_ingestion_delay_days": 2,
            }
        ],
        "return_events": [
            {
                **common,
                "id": "returned",
                "sale_id": "sale",
                "order_id": "order",
                "order_item_id": "item",
                "policy_id": "rule",
                "quantity": 2,
                "refund_amount": Decimal("6.34"),
                "status": "refunded",
                "returned_at": RETURNED,
                "ingested_at": AVAILABLE,
                "available_at": AVAILABLE,
                "curated_available_at": AVAILABLE,
                "source_record_sha256": "1" * 64,
                "returns_policy_version": "retail-returns-1.0.0",
                "mapped_stock_location_id": "original-stock",
            }
        ],
    }


def test_return_day_tail_original_stock_and_exact_money(tables):
    result = list(Returns(tables, policy()).rows())
    assert len(result) == 2
    early, returned = result
    assert early.known_event_count == 0 and early.observed_return_units is None
    assert returned.business_date == RETURNED.date() != SOLD.date()
    assert returned.known_refunded_units == 2 and returned.known_refund_amount == "6.34"
    assert returned.source_stock_location_ids == ("original-stock",)
    assert returned.status == "insufficient_data" and returned.observed_return_units is None
    assert all(p.coverage_status == "not_qualified" for p in result)


def test_late_return_does_not_rewrite_earlier_origin_or_turn_absence_into_zero(tables):
    before = list(Returns(tables, policy(AVAILABLE - timedelta(microseconds=1))).rows())
    assert all(p.known_event_count == 0 and p.observed_return_units is None for p in before)
    at = list(Returns(tables, policy()).rows())
    assert at[-1].known_event_count == 1
    assert list(Returns(tables, policy(AVAILABLE - timedelta(microseconds=1))).rows()) == before


def test_rejected_claims_have_no_refunded_units_or_money(tables):
    row = deepcopy(tables["return_events"][0])
    row.update(
        id="rejected",
        status="rejected",
        quantity=1,
        refund_amount=Decimal("0.00"),
        source_record_sha256="2" * 64,
    )
    tables["return_events"].append(row)
    point = list(Returns(tables, policy()).rows())[-1]
    assert point.known_event_count == 2 and point.known_rejected_units == 1
    assert point.known_refunded_units == 2 and point.known_refund_amount == "6.34"


@pytest.mark.parametrize(
    "fault",
    [
        "overclaim",
        "refund",
        "rejected_refund",
        "wrong_stock",
        "wrong_shop",
        "wrong_line",
        "wrong_order",
        "wrong_category",
        "wrong_currency",
        "unknown_sale",
        "policy_future",
        "purchase_future",
        "too_early",
        "too_late",
        "ingestion_delay",
        "clock",
        "availability",
        "duplicate",
    ],
)
def test_invalid_operational_return_is_rejected(tables, fault):
    event = tables["return_events"][0]
    if fault == "overclaim":
        event.update(quantity=5, refund_amount=Decimal("15.85"))
    elif fault == "refund":
        event["refund_amount"] = Decimal("6.35")
    elif fault == "rejected_refund":
        event["status"] = "rejected"
    elif fault == "wrong_stock":
        event["mapped_stock_location_id"] = "current-stock"
    elif fault == "wrong_shop":
        event["selling_location_id"] = "current-shop"
    elif fault == "wrong_line":
        event["order_item_id"] = "other-item"
    elif fault == "wrong_order":
        event["order_id"] = "other-order"
    elif fault == "wrong_category":
        tables["return_policies"][0]["category_id"] = "other-category"
    elif fault == "wrong_currency":
        event["currency"] = "EUR"
    elif fault == "unknown_sale":
        event["sale_id"] = "other-sale"
    elif fault == "policy_future":
        tables["return_policies"][0]["curated_available_at"] = AVAILABLE + timedelta(seconds=1)
    elif fault == "purchase_future":
        tables["inventory_sales"][0]["curated_available_at"] = AVAILABLE + timedelta(seconds=1)
    elif fault == "too_early":
        event["returned_at"] = SOLD + timedelta(hours=23)
    elif fault == "too_late":
        tables["return_policies"][0]["window_days"] = 1
    elif fault == "ingestion_delay":
        tables["return_policies"][0]["max_ingestion_delay_days"] = 1
    elif fault == "clock":
        event["ingested_at"] = RETURNED - timedelta(seconds=1)
    elif fault == "availability":
        event["curated_available_at"] = AVAILABLE - timedelta(seconds=1)
    else:
        tables["return_events"].append(deepcopy(event))
    with pytest.raises(SnapshotError):
        list(Returns(tables, policy()).rows())


def test_cumulative_refunded_and_rejected_claims_cannot_exceed_purchase(tables):
    extra = deepcopy(tables["return_events"][0])
    extra.update(id="other", status="rejected", quantity=3, refund_amount=Decimal("0.00"))
    tables["return_events"].append(extra)
    with pytest.raises(SnapshotError, match="quantity_or_refund"):
        Returns(tables, policy())


def test_future_event_and_unreviewed_labels_cannot_change_past_point(tables):
    origin = AVAILABLE - timedelta(microseconds=1)
    before = list(Returns(tables, policy(origin)).rows())
    tables["return_events"][0].update(quantity=400, anomaly_label=True, injection_magnitude=1000)
    assert list(Returns(tables, policy(origin)).rows()) == before
    assert "anomaly_label" not in Point.model_fields


@pytest.mark.parametrize("change", ["oversized", "reverse", "future", "timezone"])
def test_policy_rejects_unbounded_or_noncausal_origin(change):
    value = policy().model_dump(mode="json")
    if change == "oversized":
        value["start_date"] = "2020-01-01"
    elif change == "reverse":
        value["end_date"] = "2026-07-31"
    elif change == "future":
        value["end_date"] = "2026-09-01"
    else:
        value["as_of_time"] = "2026-08-04T11:00:00+02:00"
    import json

    with pytest.raises(ValidationError):
        Policy.model_validate_json(json.dumps(value))
