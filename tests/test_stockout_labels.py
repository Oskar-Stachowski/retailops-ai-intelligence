"""Boundaries, mature negatives, causal origin state and independent physical replay."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.stockout.contract import (
    Eligibility,
    LabelPoint,
    LabelPolicy,
    LedgerMovement,
    StockState,
)
from retailops_ai.stockout.dataset import build_labels, verify_labels, write_labels
from retailops_ai.stockout.labels import label_window

ORIGIN = datetime(2026, 3, 28, 23, 59, 59, 999999, tzinfo=UTC)
END = ORIGIN + timedelta(days=7)
OPENING = ORIGIN - timedelta(days=2)


def movement(delta, stamp, sequence, kind, **changes):
    return LedgerMovement.model_validate(
        {
            "product_id": "p",
            "stock_location_id": "warehouse",
            "event_id": f"event-{sequence}",
            "occurred_at": stamp,
            "available_at": stamp,
            "sequence": sequence,
            "quantity_delta": delta,
            "movement_type": kind,
            **changes,
        }
    )


def state(quantity=10, **changes):
    return StockState.model_validate(
        {
            "product_id": "p",
            "stock_location_id": "warehouse",
            "snapshot_at": ORIGIN,
            "available_at": ORIGIN,
            "status": "known",
            "on_hand": quantity,
            "reserved_qty": 0,
            "available_qty": quantity,
            **changes,
        }
    )


def coverage(**changes):
    return Eligibility.model_validate(
        {
            "covered_from_at": OPENING,
            "covered_through_at": END + timedelta(microseconds=1),
            "available_at": END,
            **changes,
        }
    )


def label(events=(), *, stock=None, evidence=None, evaluated_at=END, **kwargs):
    return label_window(
        stock or state(),
        [movement(10, OPENING, 0, "opening_stock"), *events],
        as_of=ORIGIN,
        evaluated_at=evaluated_at,
        eligibility=evidence or coverage(),
        **kwargs,
    )


@pytest.mark.parametrize("offset,expected", [(timedelta(microseconds=-1), 0), (timedelta(), 1)])
def test_right_boundary_and_instantaneous_zero(offset, expected):
    onset = END - offset
    events = [movement(-10, onset, 1, "sale"), movement(10, onset, 2, "replenishment_received")]
    result = label(events)
    assert result.status == "evaluable"
    assert result.incident_stockout == expected
    assert result.first_incident_at == (onset if expected else None)


def test_zero_at_origin_is_already_stockout_not_future_positive():
    result = label([movement(-10, ORIGIN, 1, "sale")], stock=state(0))
    assert result.status == "already_stockout"
    assert result.incident_stockout is None and result.label_available_at is None
    assert not result.eligible_at(END)


def test_complete_negative_and_training_label_maturity():
    result = label()
    assert result.incident_stockout == 0
    assert result.label_available_at == END
    assert not result.eligible_at(END - timedelta(microseconds=1))
    assert result.eligible_at(END)


@pytest.mark.parametrize("covered_through", [ORIGIN, END])
def test_incomplete_tail_is_not_negative(covered_through):
    result = label(evidence=coverage(covered_through_at=covered_through))
    assert result.status == "not_evaluable" and result.incident_stockout is None
    assert result.reason == "inventory_coverage_incomplete"


def test_late_event_delays_label_without_changing_origin_stock():
    late = END + timedelta(days=2)
    event = movement(-10, ORIGIN + timedelta(days=1), 1, "sale", available_at=late)
    pending = label([event])
    mature = label([event], evaluated_at=late)
    assert pending.reason == "outcomes_not_available" and pending.incident_stockout is None
    assert pending.label_available_at == late
    assert mature.incident_stockout == 1 and mature.label_available_at == late


def test_delayed_origin_fact_makes_historical_label_unqualified():
    event = movement(
        -5, ORIGIN - timedelta(hours=1), 1, "sale", available_at=ORIGIN + timedelta(hours=1)
    )
    result = label([event])
    assert result.reason == "origin_state_unavailable" and result.incident_stockout is None


@pytest.mark.parametrize(
    "reason",
    [
        "inactive_lifecycle",
        "window_inactive_lifecycle",
        "sales_coverage_incomplete",
        "incomplete_window",
    ],
)
def test_inactive_or_missing_coverage_is_not_a_negative(reason):
    result = label(evidence=coverage(reason=reason))
    assert result.reason == reason and result.incident_stockout is None


def test_inactive_origin_precedes_current_stockout_status():
    result = label(
        [movement(-10, ORIGIN, 1, "sale")],
        stock=state(0),
        evidence=coverage(reason="inactive_lifecycle"),
    )
    assert result.status == "not_evaluable" and result.reason == "inactive_lifecycle"


@pytest.mark.parametrize("age,expected", [(86400, "evaluable"), (86401, "not_evaluable")])
def test_snapshot_freshness_boundary(age, expected):
    snapshot_at = ORIGIN - timedelta(seconds=age)
    result = label(stock=state(snapshot_at=snapshot_at, available_at=snapshot_at))
    assert result.status == expected
    if expected == "not_evaluable":
        assert result.reason == "stale_inventory_snapshot"


def test_unknown_stock_is_not_zero():
    unknown = state(None, status="not_available", available_at=None, reserved_qty=None)
    result = label(stock=unknown)
    assert result.reason == "inventory_unknown" and result.incident_stockout is None


def test_future_or_unavailable_snapshot_is_not_used():
    assert (
        label(stock=state(available_at=ORIGIN + timedelta(seconds=1))).reason == "inventory_unknown"
    )
    future = ORIGIN + timedelta(days=1)
    assert label(stock=state(snapshot_at=future, available_at=future)).reason == "inventory_unknown"


def test_known_revision_after_older_snapshot_is_replayed_at_origin():
    snapshot_at = ORIGIN - timedelta(hours=2)
    event = movement(
        -4, ORIGIN - timedelta(hours=3), 1, "sale", available_at=ORIGIN - timedelta(hours=1)
    )
    # The old snapshot did not yet know the sale; origin does know it.
    result = label([event], stock=state(snapshot_at=snapshot_at, available_at=snapshot_at))
    assert result.incident_stockout == 0


def test_permutation_and_future_movements_do_not_change_past_label():
    events = [
        movement(-10, ORIGIN + timedelta(days=1), 1, "sale"),
        movement(12, ORIGIN + timedelta(days=2), 2, "replenishment_received"),
    ]
    baseline = label(events)
    assert label(tuple(reversed(events))) == baseline
    assert label([*events, movement(-12, END + timedelta(seconds=1), 3, "sale")]) == baseline


def test_shared_channels_cannot_duplicate_physical_label_grain():
    with pytest.raises(ValueError, match="physical_grain"):
        label([movement(-1, ORIGIN + timedelta(days=1), 1, "sale", stock_location_id="other")])
    with pytest.raises(ValidationError, match="extra_forbidden"):
        StockState.model_validate({**state().model_dump(), "channel": "online"})


@pytest.mark.parametrize(
    "events,code",
    [
        ([movement(-11, ORIGIN + timedelta(days=1), 1, "sale")], "negative_balance"),
        ([movement(1, ORIGIN + timedelta(days=1), 1, "opening_stock")], "duplicate_opening"),
        ([movement(-1, ORIGIN + timedelta(days=1), 1, "sale")] * 2, "duplicate_movement"),
    ],
)
def test_invalid_ledger_rejected(events, code):
    with pytest.raises(ValueError, match=code):
        label(events)


def test_stock_snapshot_reconciles_with_ledger():
    with pytest.raises(ValueError, match="snapshot_ledger_mismatch"):
        label(stock=state(9))


def test_timezone_dst_uses_utc_elapsed_seven_days():
    result = label()
    assert (result.window_end_at - result.as_of).total_seconds() == 7 * 86400
    with pytest.raises(ValidationError, match="utc_timestamp_required"):
        LabelPoint.model_validate({**result.model_dump(), "as_of": "2026-03-29T01:59:59+01:00"})


@pytest.mark.parametrize(
    "kind,delta", [("sale", 1), ("sale", 0), ("return_to_stock", -1), ("inventory_adjustment", 0)]
)
def test_movement_sign_is_not_a_free_form_quantity(kind, delta):
    with pytest.raises(ValidationError, match="movement_required|adjustment_required"):
        movement(delta, END, 1, kind)


def test_reserved_stock_and_boolean_quantity_are_not_accepted():
    with pytest.raises(ValidationError):
        state(10, reserved_qty=2, available_qty=8)
    with pytest.raises(ValidationError):
        state(True)


def test_training_cutoff_requires_utc():
    with pytest.raises(ValueError, match="utc_timestamp_required"):
        label().eligible_at(END.replace(tzinfo=None))


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    root = tmp_path_factory.mktemp("stockout-native")
    with ZipFile(Path(__file__).parents[1] / "data/fixtures/inventory-v1_1.zip") as archive:
        archive.extractall(root)
    document = build_labels(root / "private", allow_evaluation_truth=True)
    return root, document


def test_native_private_labels_match_ledger_and_have_no_model_claim(native, tmp_path):
    root, document = native
    assert len(document["points"]) == 60
    assert document["report"]["positive_labels"] > 0
    assert document["report"]["negative_labels"] > 0
    assert document["report"]["model_ready"] is False
    target = tmp_path / "labels.json"
    assert write_labels(document, target) == "published"
    before = target.read_bytes()
    assert write_labels(document, target) == "reused" and target.read_bytes() == before
    assert verify_labels(target, root / "private", allow_evaluation_truth=True) == document


def test_private_opt_in_and_public_snapshot_rejected(native):
    root, _ = native
    with pytest.raises(SnapshotError, match="opt_in"):
        build_labels(root / "private")
    with pytest.raises(SnapshotError, match="private_snapshot"):
        build_labels(root / "facts", allow_evaluation_truth=True)


def test_resealed_false_label_does_not_pass_full_replay(native, tmp_path):
    root, original = native
    document = json.loads(canonical_json(original))
    row = next(p for p in document["points"] if p["incident_stockout"] == 0)
    row["incident_stockout"] = 1
    # Reseal the content and ID as an attacker could; verification must recompute the source.
    import hashlib

    document["descriptor"]["points_sha256"] = hashlib.sha256(
        canonical_json(document["points"])
    ).hexdigest()
    document["label_dataset_id"] = (
        "labels-sha256-" + hashlib.sha256(canonical_json(document["descriptor"])).hexdigest()
    )
    target = tmp_path / "labels.json"
    target.write_bytes(canonical_json(document))
    with pytest.raises(SnapshotError, match="full_replay_mismatch"):
        verify_labels(target, root / "private", allow_evaluation_truth=True)


def test_different_policy_changes_identity_and_output_never_overwrites(native, tmp_path):
    root, document = native
    changed = build_labels(
        root / "private", allow_evaluation_truth=True, policy=LabelPolicy(max_windows=100)
    )
    assert changed["label_dataset_id"] != document["label_dataset_id"]
    target = tmp_path / "labels.json"
    write_labels(document, target)
    with pytest.raises(SnapshotError, match="immutable_output_conflict"):
        write_labels(changed, target)
    assert json.loads(target.read_bytes()) == document


def test_symlink_output_is_refused(native, tmp_path):
    _, document = native
    real = tmp_path / "real.json"
    real.write_text("untouched")
    link = tmp_path / "labels.json"
    link.symlink_to(real)
    with pytest.raises(SnapshotError, match="symlink_output"):
        write_labels(document, link)
    assert real.read_text() == "untouched"
