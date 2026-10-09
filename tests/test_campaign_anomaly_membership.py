"""Complete native membership, causal vectors and explicit non-training roles."""

import json
from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from test_anomaly_detectors import point, protocol, scope

from retailops_ai.anomaly_detectors.protocol import point_key, requested, series_key
from retailops_ai.anomaly_portfolio.model import count_rate_row
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_anomaly_membership import (
    CampaignAnomalyMembershipPlan,
    iter_anomaly_membership_census,
)


def plan(scopes=None, **changes):
    native = protocol((scopes[0],) if scopes else None)
    values = {
        k: getattr(native, k)
        for k in (
            "scopes",
            "train",
            "validation",
            "test",
            "training_cutoff",
            "selection_cutoff",
        )
    }
    if scopes is not None:
        values["scopes"] = scopes
    return CampaignAnomalyMembershipPlan(
        **(
            values
            | {
                "feature_plan_sha256": "1" * 64,
                "native_points_sha256": "2" * 64,
                "max_requested_rows": 20000000,
            }
            | changes
        )
    )


@pytest.fixture(scope="module")
def complete_case():
    cfg = plan(tuple(sorted((scope(), scope("return_completed")), key=series_key)))
    days = (cfg.test.end - cfg.train.start).days + 7
    values = [
        point(
            cfg.train.start - timedelta(days=6) + timedelta(days=i),
            12 + i % 7,
            s.event_type,
            s.currency,
        )
        for s in cfg.scopes
        for i in range(days)
    ]
    return cfg, values


def test_complete_membership_and_vectors_equal_original_native_pipeline(complete_case):
    cfg, values = complete_case
    actual = list(iter_anomaly_membership_census(iter(values), cfg))
    native = protocol(cfg.scopes)
    expected = requested(native, values)
    indexed = {(*series_key(p), p.business_date): p for p in values}
    assert len(actual) == len(expected) == cfg.rows
    assert [r.membership for r in actual] == [m for m, _ in expected]
    assert {r.membership.role for r in actual} == {"train", "gap", "validation", "test"}
    for row, (membership, p) in zip(actual, expected, strict=True):
        assert row.public_point_sha256 == canonical_sha256(p.model_dump(mode="json"))
        if membership.eligible and membership.role in ("train", "validation"):
            assert row.training_or_validation_row == count_rate_row(p, indexed)
        else:
            assert row.training_or_validation_row is None
    late_returns = [
        r for r in actual if "outcome_after_training_cutoff" in r.membership.reason_codes
    ]
    assert late_returns and all(r.training_or_validation_row is None for r in late_returns)


def test_later_validation_and_test_values_cannot_change_training(complete_case):
    cfg, values = complete_case
    changed = [
        point(p.business_date, 99999, p.event_type, p.currency)
        if p.business_date >= cfg.validation.start
        else p
        for p in values
    ]
    before = [
        r for r in iter_anomaly_membership_census(values, cfg) if r.membership.role == "train"
    ]
    after = [
        r for r in iter_anomaly_membership_census(changed, cfg) if r.membership.role == "train"
    ]
    assert before == after


def test_missing_declarations_remain_counted_unknown(complete_case):
    cfg, values = complete_case
    selected = [p for p in values if p.business_date.day % 3]
    present = {point_key(p) for p in selected}
    actual = list(iter_anomaly_membership_census(selected, cfg))
    absent = [
        r
        for r in actual
        if (*series_key(r.membership), r.membership.business_date.isoformat()) not in present
    ]
    assert len(actual) == cfg.rows and absent
    assert all(
        r.membership.input_status == "no_declaration"
        and not r.membership.eligible
        and r.training_or_validation_row is None
        and r.public_point_sha256 is None
        for r in absent
    )


def test_full_population_above_legacy_cap_includes_every_scope_day():
    scopes = tuple(
        sorted((scope(product=str(UUID(int=i + 10))) for i in range(200)), key=series_key)
    )
    cfg = plan(scopes)
    rows = list(iter_anomaly_membership_census(iter(()), cfg))
    assert len(rows) == cfg.rows > 10000
    assert len({(series_key(r.membership), r.membership.business_date) for r in rows}) == cfg.rows
    assert all(r.membership.input_status == "no_declaration" for r in rows)


@pytest.mark.parametrize("case", ["duplicate", "reverse", "foreign", "early", "late"])
def test_wrong_point_inventory_cannot_complete(case, complete_case):
    cfg, values = complete_case
    if case == "duplicate":
        values = [values[0], *values]
    elif case == "reverse":
        values = values[::-1]
    elif case == "foreign":
        values = [point(cfg.train.start, 12, currency="EUR")]
    elif case == "early":
        values = [point(cfg.train.start - timedelta(days=7))]
    else:
        values = [point(cfg.test.end + timedelta(days=1))]
    with pytest.raises(ValueError, match="point_order_scope_or_window"):
        list(iter_anomaly_membership_census(values, cfg))


def test_series_byte_budget_fails_without_accepting_a_prefix(complete_case):
    cfg, values = complete_case
    raw = cfg.model_dump(mode="json") | {"max_series_bytes": 4096}
    small = CampaignAnomalyMembershipPlan.model_validate_json(json.dumps(raw))
    with pytest.raises(ValueError, match="series_byte_budget"):
        list(iter_anomaly_membership_census(values, small))


@pytest.mark.parametrize("case", ["order", "duplicate", "overlap", "clock", "budget", "final"])
def test_invalid_scope_split_budget_and_final_plan_rejected(case):
    cfg = plan()
    values = cfg.model_dump(mode="json")
    if case == "order":
        values["scopes"] = [
            scope().model_dump(mode="json"),
            scope("return_completed").model_dump(mode="json"),
        ]
    elif case == "duplicate":
        values["scopes"] *= 2
    elif case == "overlap":
        values["validation"]["start"] = values["train"]["end"]
    elif case == "clock":
        values["training_cutoff"] = values["selection_cutoff"]
    elif case == "budget":
        values["max_requested_rows"] = 1
    else:
        values["phase"] = "final"
    with pytest.raises(ValidationError):
        CampaignAnomalyMembershipPlan.model_validate_json(json.dumps(values))
