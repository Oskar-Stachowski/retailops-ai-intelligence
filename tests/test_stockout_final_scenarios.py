"""Future plan revisions and shared stock never change historical treatment membership."""

import hashlib
from copy import deepcopy
from datetime import UTC, date, datetime

import pytest

from retailops_ai.stockout_campaign.scenarios import (
    active_versions,
    memberships,
    promotion_exposure,
)

ORIGIN = datetime(2026, 7, 25, 23, 59, 59, tzinfo=UTC)


def row(product="sku", stock="warehouse", *, constraint=0):
    return dict(
        product_id=product,
        stock_location_id=stock,
        as_of=ORIGIN.isoformat(),
        values=dict(history_constrained_days=constraint),
    )


def facts():
    common = dict(
        curated_available_at=datetime(2026, 7, 1, tzinfo=UTC),
        effective_from=date(2026, 7, 28),
        effective_to=date(2026, 8, 4),
        version=1,
    )
    return dict(
        promotion_plans=[
            dict(
                common,
                promotion_key="promo",
                product_id="sku",
                scope="location_channel",
                selling_location_id="shop",
                channel="store",
                status="active",
            )
        ],
        fulfillment_routes=[
            dict(
                common,
                route_key="route",
                selling_location_id="shop",
                channel="store",
                stock_location_id="warehouse",
            )
        ],
        assortment=[
            dict(
                common,
                assortment_key="assortment",
                product_id="sku",
                selling_location_id="shop",
                channel="store",
            )
        ],
    )


def test_promotion_requires_same_physical_route_channel_and_assortment():
    tables = facts()
    assert promotion_exposure(row(), tables)
    assert not promotion_exposure(row(stock="different-stock"), tables)
    assert not promotion_exposure(row(product="other-sku"), tables)
    for table, field, value in [
        ("fulfillment_routes", "channel", "online"),
        ("assortment", "channel", "online"),
        ("promotion_plans", "selling_location_id", "different-shop"),
    ]:
        changed = deepcopy(tables)
        changed[table][0][field] = value
        assert not promotion_exposure(row(), changed)


def test_later_revisions_do_not_change_prior_exposure_or_controls():
    original = facts()
    changed = deepcopy(original)
    changed["promotion_plans"].append(
        dict(
            original["promotion_plans"][0],
            version=2,
            status="cancelled",
            curated_available_at=datetime(2026, 7, 26, tzinfo=UTC),
        )
    )
    assert promotion_exposure(row(), changed)
    assert memberships(
        [row(), row(product="other")], world="future_stress", tables=original
    ) == memberships([row(), row(product="other")], world="future_stress", tables=changed)
    changed["promotion_plans"][-1]["curated_available_at"] = datetime(2026, 7, 25, tzinfo=UTC)
    assert not promotion_exposure(row(), changed)
    future = facts()
    future["promotion_plans"][0]["curated_available_at"] = datetime(2026, 7, 26, tzinfo=UTC)
    assert memberships([row()], world="matching", tables=future)[0]["control:promotion"] == []


def test_fixed_shock_membership_matching_controls_and_explicit_overlaps():
    treated = next(
        str(i) for i in range(20) if int(hashlib.sha256(str(i).encode()).hexdigest(), 16) % 3 == 0
    )
    control = next(
        str(i) for i in range(20) if int(hashlib.sha256(str(i).encode()).hexdigest(), 16) % 3 != 0
    )
    rows = [row(treated, constraint=1), row(control)]
    groups, overlaps = memberships(rows, world="future_stress", tables=facts())
    assert groups["demand_shock"] == [0] and groups["control:demand_shock"] == [1]
    assert groups["normal"] == [1] and groups["inventory_constraint"] == [0]
    assert overlaps["demand_shock&inventory_constraint"] == 1
    matching, _ = memberships(rows, world="matching", tables=facts())
    assert matching["demand_shock"] == matching["control:demand_shock"] == []


def test_unknown_or_ambiguous_known_plan_version_is_rejected():
    table = facts()["promotion_plans"]
    table.append(dict(table[0], scope="global"))
    with pytest.raises(ValueError, match="ambiguous"):
        active_versions(table, "promotion_key", ORIGIN, date(2026, 7, 30))
    with pytest.raises(ValueError, match="scenario_inputs"):
        memberships([row()], world="invented", tables=facts())
