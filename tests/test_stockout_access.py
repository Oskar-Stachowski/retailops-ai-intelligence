"""Stock-location authority is explicit and never inherited from selling locations."""

import secrets
from datetime import UTC, datetime, timedelta

import pytest

from retailops_ai.domain.access import Principal, StockoutAccess, can_read_stockout
from retailops_ai.security.local import LocalAccess, token_fingerprint
from retailops_ai.security.models import (
    AccessGrant,
    AccessPolicy,
    Credential,
    ResourceScope,
    StockoutResourceScope,
)


def grant(**updates):
    return AccessGrant(
        **{
            "principal_id": "reader",
            "roles": ["viewer"],
            "capabilities": ["stockout:read"],
            "scope": None,
            "stockout_scope": StockoutResourceScope(product_ids=["p1"], stock_location_ids=["wh1"]),
            **updates,
        }
    )


def test_stockout_capability_requires_an_explicit_physical_scope():
    with pytest.raises(ValueError, match="explicit_physical_scope"):
        grant(stockout_scope=None)
    with pytest.raises(ValueError, match="explicit_physical_scope"):
        grant(capabilities=["access:admin"], roles=["admin"])


def test_stockout_run_requires_pipeline_role():
    with pytest.raises(ValueError, match="pipeline_role"):
        grant(capabilities=["stockout:run"])
    assert grant(capabilities=["stockout:run"], roles=["pipeline"]).stockout_scope is not None


@pytest.mark.parametrize(
    "fields",
    [
        dict(product_ids=["p", "p"], stock_location_ids=["wh"]),
        dict(product_ids=["p"], stock_location_ids=["wh", "wh"]),
    ],
)
def test_duplicate_scope_members_cannot_double_shared_stock(fields):
    with pytest.raises(ValueError, match="duplicate_stockout"):
        StockoutResourceScope(**fields)


def test_selling_location_or_forecast_capability_does_not_authorize_stockout():
    p = Principal(
        "user",
        frozenset({"viewer"}),
        frozenset({"forecast:read"}),
        frozenset({"p1"}),
        frozenset({"wh1"}),
        frozenset({"store"}),
    )
    assert not can_read_stockout(p, products={"p1"}, stock_locations={"wh1"})
    p = Principal(
        "user",
        p.roles,
        frozenset({"stockout:read"}),
        p.product_ids,
        p.selling_location_ids,
        p.channels,
    )
    assert not can_read_stockout(p, products={"p1"}, stock_locations={"wh1"})


def test_forecast_and_stockout_product_authority_are_independent():
    p = Principal(
        "user",
        frozenset({"viewer"}),
        frozenset({"forecast:read", "stockout:read"}),
        frozenset({"forecast-product"}),
        frozenset({"selling"}),
        frozenset({"store"}),
        stockout=StockoutAccess(frozenset({"stock-product"}), frozenset({"wh1"})),
    )
    assert can_read_stockout(p, products={"stock-product"}, stock_locations={"wh1"})
    assert not can_read_stockout(p, products={"forecast-product"}, stock_locations={"wh1"})
    assert not can_read_stockout(p, products={"stock-product"}, stock_locations={"selling"})
    assert not can_read_stockout(p, products=set(), stock_locations={"wh1"})
    assert not can_read_stockout(p, products={"stock-product"}, stock_locations=set())


def test_local_authentication_retains_the_physical_scope():
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    policy = AccessPolicy(
        schema_version="1.0",
        policy_id="test",
        grants=[grant()],
        credentials=[
            Credential(
                principal_id="reader",
                token_sha256=token_fingerprint(token),
                not_before=now - timedelta(minutes=1),
                expires_at=now + timedelta(hours=1),
                revoked=False,
            )
        ],
    )
    principal = LocalAccess(policy).authenticate("Bearer " + token, now=now)
    assert principal is not None
    assert principal.stockout == StockoutAccess(frozenset({"p1"}), frozenset({"wh1"}))
    assert principal.product_ids == frozenset() and principal.selling_location_ids == frozenset()
    assert can_read_stockout(principal, products={"p1"}, stock_locations={"wh1"})


def test_legacy_grant_serialization_keeps_the_original_wire_shape():
    g = grant(
        capabilities=["forecast:read"],
        stockout_scope=None,
        scope=ResourceScope(product_ids=["p1"], selling_location_ids=["s1"], channels=["store"]),
    )
    assert "stockout_scope" not in g.model_dump(mode="json")
    assert g.model_dump(mode="json")["scope"]["selling_location_ids"] == ["s1"]
