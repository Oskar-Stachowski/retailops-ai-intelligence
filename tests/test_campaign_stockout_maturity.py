"""Real exposed Source joins plus separate synthetic role/maturity controls."""

import hashlib
import json
import shutil
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_campaign_anomaly_parent import public_parent as public_parent  # noqa: F401
from test_campaign_stockout_labels import qualified as qualified  # noqa: F401
from test_campaign_stockout_labels import reference
from test_campaign_stockout_public import prepared as prepared  # noqa: F401

from retailops_ai.data_contracts.common import DateWindow, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_stockout_maturity as component
from retailops_ai.evaluation_campaign.campaign_stockout_labels import CampaignStockoutLabelPlan
from retailops_ai.evaluation_campaign.campaign_stockout_maturity_contract import (
    STOCKOUT_ROLES,
    CampaignStockoutMaturityPlan,
    CampaignStockoutMaturityPolicy,
)
from retailops_ai.evaluation_campaign.campaign_stockout_public_contract import (
    CampaignStockoutPublicRow,
)
from retailops_ai.evaluation_campaign.partition_contract import ROLES, RoleWindow
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout.contract import LabelPoint
from retailops_ai.stockout_training.inputs import keys_sha256, labels_sha256


def policy(start=date(2026, 1, 1)):
    return CampaignStockoutMaturityPolicy(
        windows=tuple(
            RoleWindow(
                role=role,
                origins=DateWindow(
                    start=start + timedelta(days=25 * i),
                    end=start + timedelta(days=25 * i + 9),
                ),
                label_knowledge_cutoff=end_of_day(start + timedelta(days=25 * i + 23)),
            )
            for i, role in enumerate(ROLES)
        )
    )


def feature(origin, *, product="product", **updates):
    return CampaignStockoutPublicRow(
        **{
            "product_id": product,
            "stock_location_id": "stock",
            "as_of": origin,
            "status": "eligible",
            "reason": None,
            "category_id": "category",
            "category_source_record_sha256": "a" * 64,
            "feature_sha256": "b" * 64,
            "upstream_sha256": "c" * 64,
            "values": dict(
                available_qty=10,
                snapshot_age_hours=0.0,
                history_known_days=28,
                history_missing_days=0,
                observed_sales_mean=1.0,
                observed_sales_std=0.0,
                in_stock_sales_mean=1.0,
                history_in_stock_days=28,
                history_constrained_days=0,
                history_inventory_unknown_days=0,
                historical_stockout_onsets=0,
                days_of_supply_observed=10.0,
                days_of_supply_in_stock=10.0,
                open_order_quantity=0,
                due_within_7d_quantity=0,
                overdue_order_quantity=0,
                next_expected_delivery_hours=None,
                quoted_lead_time_days=None,
                forecast_units_7d=7.0,
                forecast_days_of_supply=10.0,
                forecast_unavailable=0,
            ),
            **updates,
        }
    )


def label(origin, *, product="product", **updates):
    return LabelPoint(
        **{
            "product_id": product,
            "stock_location_id": "stock",
            "as_of": origin,
            "window_end_at": origin + timedelta(days=7),
            "evaluated_at": origin + timedelta(days=60),
            "status": "evaluable",
            "reason": None,
            "incident_stockout": 0,
            "label_available_at": origin + timedelta(days=8),
            "first_incident_at": None,
            "first_incident_event_id": None,
            **updates,
        }
    )


@pytest.mark.parametrize("position", range(5))
def test_shared_dates_have_explicit_different_stockout_purposes(position):
    frozen = policy()
    window = frozen.windows[position]
    origin = end_of_day(window.origins.start)
    result = component._membership(feature(origin), label(origin), frozen)
    assert result == dict(
        role=STOCKOUT_ROLES[position],
        development_window=ROLES[position],
        eligible=True,
        reason=None,
        label_knowledge_cutoff=window.model_dump(mode="json")["label_knowledge_cutoff"],
    )


@pytest.mark.parametrize("delta,eligible", [(0, True), (1, False)])
def test_availability_cutoff_is_exact_including_last_evaluation_role(delta, eligible):
    frozen = policy()
    for window in frozen.windows:
        origin = end_of_day(window.origins.start)
        point = label(
            origin,
            label_available_at=window.label_knowledge_cutoff + timedelta(microseconds=delta),
        )
        result = component._membership(feature(origin), point, frozen)
        assert result["eligible"] is eligible
        assert result["reason"] == (None if eligible else "label_not_available_at_role_cutoff")


@pytest.mark.parametrize(
    "status", ["already_stockout", "not_evaluable", "feature_missing", "purged"]
)
def test_unknown_labels_and_ineligible_features_are_not_negatives(status):
    frozen = policy()
    origin = end_of_day(frozen.windows[0].origins.start)
    f, point = feature(origin), label(origin)
    if status in {"already_stockout", "not_evaluable"}:
        point = label(
            origin,
            status=status,
            reason="missing_history" if status == "not_evaluable" else None,
            incident_stockout=None,
            label_available_at=None,
        )
    elif status == "feature_missing":
        f = feature(origin, status="insufficient_data", reason="missing_history")
    else:
        origin += timedelta(days=15)
        f, point = feature(origin), label(origin)
    result = component._membership(f, point, frozen)
    assert result["eligible"] is False and result["reason"]
    if status == "purged":
        assert result["role"] == "purged" and result["development_window"] is None


@pytest.mark.parametrize("mutation", ["order", "purpose", "purge", "horizon", "boundary", "clock"])
def test_policy_rejects_incompatible_mapping_overlap_and_unmature_windows(mutation):
    state = policy().model_dump(mode="python")
    windows = list(state["windows"])
    if mutation == "order":
        windows[0], windows[1] = windows[1], windows[0]
    elif mutation == "purpose":
        state["stockout_purposes"] = tuple(reversed(STOCKOUT_ROLES))
    elif mutation == "purge":
        state["purge_days"] = 14
    elif mutation == "horizon":
        windows[0]["label_knowledge_cutoff"] = end_of_day(windows[0]["origins"]["end"])
    elif mutation == "boundary":
        windows[0]["label_knowledge_cutoff"] = datetime.combine(
            windows[1]["origins"]["start"], datetime.min.time(), UTC
        )
    else:
        windows[0]["label_knowledge_cutoff"] = datetime(2026, 1, 24)
    state["windows"] = tuple(windows)
    with pytest.raises(ValueError):
        CampaignStockoutMaturityPolicy.model_validate(state)


def reader(public_parent, prepared, qualified, scratch, **updates):
    target, public_plan, manifest = prepared
    first = json.loads((target / "rows.jsonl").read_bytes().splitlines()[0])
    plan = CampaignStockoutMaturityPlan(
        **{
            "public_dataset_id": manifest["dataset_id"],
            "public": public_plan,
            "labels": CampaignStockoutLabelPlan(
                source=public_plan.parent.source, runtime=public_plan.parent.runtime
            ),
            "policy": policy(datetime.fromisoformat(first["as_of"]).date()),
            **updates,
        }
    )
    return component.CampaignStockoutMatureRows(
        target, *public_parent[:2], qualified[0], plan, scratch, allow_evaluation_truth=True
    )


def test_real_full_sources_preserve_every_original_feature_label_and_role(
    public_parent, prepared, qualified, tmp_path
):
    expected_labels = reference(public_parent, qualified)
    expected_features = [
        json.loads(line) for line in (prepared[0] / "rows.jsonl").read_bytes().splitlines()
    ]
    adapter = reader(public_parent, prepared, qualified, tmp_path)
    with adapter:
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
        actual = list(adapter.rows())
        assert [r["feature"] for r in actual] == expected_features
        assert [r["label"] for r in actual] == expected_labels
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
    receipt = adapter.receipt()
    assert receipt["rows"] == len(expected_labels)
    assert (
        receipt["stream_sha256"]
        == hashlib.sha256(b"".join(canonical_bytes(r) + b"\n" for r in actual)).hexdigest()
    )
    assert receipt["original_labels"]["rows"] == receipt["rows"]
    assert receipt["mature_role_membership_verified"]
    assert sum(receipt["eligible_by_role"].values()) + sum(
        receipt["ineligible_reasons"].values()
    ) == len(actual)
    assert set(receipt["eligible_by_role"]) == set(STOCKOUT_ROLES)
    for role in STOCKOUT_ROLES:
        selected = [
            r for r in actual if r["membership"]["role"] == role and r["membership"]["eligible"]
        ]
        points = [r["feature"] for r in selected]
        outcomes = [r["label"]["incident_stockout"] for r in selected]
        assert receipt["eligible_role_traces"][role] == dict(
            keys_sha256=keys_sha256(points),
            labels_sha256=labels_sha256(points, outcomes),
            joined_rows_sha256=hashlib.sha256(
                b"".join(canonical_bytes(r) + b"\n" for r in selected)
            ).hexdigest(),
        )
    assert not receipt["journal_authorization_proven"] and not receipt["stage_ready"]
    receipt["rows"] = -1
    assert adapter.receipt()["rows"] == len(actual)
    with pytest.raises(SnapshotError, match="single_use"):
        adapter.__enter__()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["none", "one", "caught_repeated"])
def test_partial_and_caught_repeated_consumption_cannot_issue_receipt(
    public_parent, prepared, qualified, tmp_path, kind
):
    adapter = reader(public_parent, prepared, qualified, tmp_path)
    with pytest.raises(SnapshotError, match="complete_stream"):
        with adapter:
            if kind != "none":
                iterator = adapter.rows()
                next(iterator)
            if kind == "caught_repeated":
                with pytest.raises(SnapshotError, match="repeated_stream"):
                    list(adapter.rows())
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    if kind != "none":
        with pytest.raises((SnapshotError, ValueError)):
            next(iterator)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("when", ["after_read", "during_label_close"])
def test_public_mutation_before_final_acceptance_is_detected(
    public_parent, prepared, qualified, tmp_path, when
):
    public, scratch = tmp_path / "public", tmp_path / "scratch"
    shutil.copytree(prepared[0], public)
    scratch.mkdir()
    adapter = reader(public_parent, (public, *prepared[1:]), qualified, scratch)
    target = public / "rows.jsonl"

    def mutate():
        target.write_bytes(target.read_bytes() + b" ")

    with pytest.raises(SnapshotError, match="public_changed"):
        with adapter:
            list(adapter.rows())
            if when == "after_read":
                mutate()
            else:
                adapter._labels._stack.callback(mutate)
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(scratch.iterdir())


def test_opt_in_precedes_all_io(public_parent, prepared, qualified, tmp_path, monkeypatch):
    adapter = reader(public_parent, prepared, qualified, tmp_path)
    adapter.allow_truth = False
    monkeypatch.setattr(component, "verify_public_stockout", lambda *a, **k: pytest.fail("I/O"))
    with pytest.raises(SnapshotError, match="private_opt_in"):
        adapter.__enter__()


@pytest.mark.parametrize("mutation", ["source", "copied_bool_limit"])
def test_plan_requires_same_original_parent_and_strict_resource_types(
    public_parent, prepared, qualified, tmp_path, mutation
):
    adapter = reader(public_parent, prepared, qualified, tmp_path)
    state = adapter.plan.model_dump(mode="python")
    if mutation == "source":
        state["labels"]["source"]["snapshot_manifest_sha256"] = "f" * 64
        with pytest.raises(ValueError, match="same_source_and_runtime"):
            CampaignStockoutMaturityPlan.model_validate(state)
    else:
        copied = adapter.plan.model_copy(update={"max_rows": True})
        with pytest.raises(ValueError):
            component.CampaignStockoutMatureRows(
                prepared[0],
                *public_parent[:2],
                qualified[0],
                copied,
                tmp_path,
                allow_evaluation_truth=True,
            )


@pytest.mark.parametrize("limit", ["max_rows", "max_stream_bytes"])
def test_complete_population_limits_refuse_instead_of_truncating(
    public_parent, prepared, qualified, tmp_path, limit
):
    adapter = reader(
        public_parent,
        prepared,
        qualified,
        tmp_path,
        **{limit: 1 if limit == "max_rows" else 4096},
    )
    with pytest.raises(SnapshotError, match="full_population_budget"):
        with adapter:
            list(adapter.rows())
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(tmp_path.iterdir())


def test_changed_plan_after_consumption_cannot_issue_receipt(
    public_parent, prepared, qualified, tmp_path
):
    adapter = reader(public_parent, prepared, qualified, tmp_path)
    with pytest.raises(SnapshotError, match="plan_changed"):
        with adapter:
            list(adapter.rows())
            adapter.plan = adapter.plan.model_copy(update={"max_rows": 999999})
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()


@pytest.mark.parametrize("mutation", ["missing", "different_key"])
def test_complete_public_private_join_refuses_missing_or_different_physical_keys(
    public_parent, prepared, qualified, tmp_path, mutation
):
    adapter = reader(public_parent, prepared, qualified, tmp_path)
    with pytest.raises(SnapshotError, match="complete_physical_origin_join"):
        with adapter:
            original = adapter._labels.rows

            def changed():
                points = original()
                point = next(points)
                if mutation == "different_key":
                    point = {**point, "product_id": "changed-product"}
                    yield point
                yield from points

            adapter._labels.rows = changed
            list(adapter.rows())
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()


def test_valid_substituted_label_with_same_key_cannot_reuse_original_receipt(
    public_parent, prepared, qualified, tmp_path
):
    adapter = reader(public_parent, prepared, qualified, tmp_path)
    with pytest.raises(SnapshotError, match="original_label_receipt_mismatch"):
        with adapter:
            original = adapter._labels.rows

            def changed():
                points = original()
                point = next(points)
                point = {
                    **point,
                    "evaluated_at": (
                        datetime.fromisoformat(point["evaluated_at"]) + timedelta(seconds=1)
                    ).isoformat(),
                }
                LabelPoint.model_validate_json(canonical_bytes(point))
                yield point
                yield from points

            adapter._labels.rows = changed
            assert len(list(adapter.rows())) == prepared[2]["descriptor"]["origins"]
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()


def test_synthetic_join_keeps_more_than_10000_rows_across_every_role():
    """Pure stream/maturity proof. No Source preparation or provenance claim."""
    frozen = policy()
    count = 10001

    def origin(i):
        return end_of_day(frozen.windows[i % 5].origins.start)

    adapter = component.CampaignStockoutMatureRows.__new__(component.CampaignStockoutMatureRows)
    adapter._active = True
    adapter._started = adapter._failed = adapter._complete = adapter._exhausted = False
    adapter._plan_hash = "0" * 64
    adapter.plan = SimpleNamespace(
        policy=frozen,
        max_rows=count,
        max_stream_bytes=64 * 1024**2,
        max_record_bytes=65536,
        public_dataset_id="synthetic_unit_join_not_Source",
    )
    adapter._public_rows = lambda: (feature(origin(i), product=f"p-{i:06d}") for i in range(count))
    adapter._labels = SimpleNamespace(
        rows=lambda: (
            label(origin(i), product=f"p-{i:06d}").model_dump(mode="json") for i in range(count)
        )
    )
    counts = Counter(row["membership"]["role"] for row in adapter.rows())
    assert sum(counts.values()) == count and counts == dict(
        zip(STOCKOUT_ROLES, [2001, 2000, 2000, 2000, 2000], strict=True)
    )
    assert adapter._receipt["rows"] == count
    assert adapter._receipt["eligible_by_role"] == counts
    assert adapter._receipt["classes_by_role"]["base_train"] == {"0": 2001, "1": 0}
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
