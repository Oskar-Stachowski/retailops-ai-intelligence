"""Full live ordinary/demand/physical parents retain all causal memberships."""

from datetime import UTC, date, datetime

import pytest
from test_campaign_anomaly_days import day_case  # noqa: F401
from test_campaign_anomaly_features import feature_plan, open_gate
from test_campaign_anomaly_parent import public_parent  # noqa: F401

from retailops_ai.anomaly_detectors.census_contract import CensusFitPolicy
from retailops_ai.anomaly_detectors.protocol import Scope, Window, requested, series_key
from retailops_ai.anomaly_portfolio.model import EventCapacity, count_rate_row
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_anomaly_features import (
    CampaignAnomalyFeatureProjection,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_membership import (
    CampaignAnomalyMembershipPlan,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_training import fit_anomaly_membership_census


def test_full_public_feature_parent_matches_native_membership_and_vectors(
    day_case,  # noqa: F811
    tmp_path,  # noqa: F811 - parametrized fixture
):
    with open_gate(day_case, tmp_path) as (gate, _, _):
        adapter = CampaignAnomalyFeatureProjection(gate, feature_plan(gate), tmp_path)
        with adapter:
            points = list(adapter.points())
            scopes = {
                series_key(p): Scope.model_validate({k: getattr(p, k) for k in Scope.model_fields})
                for p in points
            }
            plan = CampaignAnomalyMembershipPlan(
                feature_plan_sha256=canonical_sha256(adapter.plan.model_dump(mode="json")),
                native_points_sha256=adapter.native_points_sha256,
                scopes=tuple(scopes[k] for k in sorted(scopes)),
                train=Window(start=date(2026, 7, 1), end=date(2026, 7, 20)),
                validation=Window(start=date(2026, 7, 23), end=date(2026, 7, 25)),
                test=Window(start=date(2026, 7, 29), end=date(2026, 7, 31)),
                training_cutoff=datetime(2026, 7, 22, tzinfo=UTC),
                selection_cutoff=datetime(2026, 7, 29, tzinfo=UTC),
                max_requested_rows=100000,
            )
            actual = list(adapter.training_memberships(plan))
            numerical = fit_anomaly_membership_census(
                adapter.training_memberships(plan),
                plan,
                CensusFitPolicy(n_estimators=8, max_samples=16),
                tuple(
                    EventCapacity(event_type=e, alert_fraction=0.05, high_fraction=0.01)
                    for e in ("sale_completed", "return_completed")
                ),
                scratch=tmp_path,
            )
            assert numerical.requested_rows == plan.rows
            assert sum(c for _, _, c in numerical.membership_counts) == plan.rows
            for group in numerical.groups:
                assert group.training_rows == sum(
                    r.membership.role == "train"
                    and r.membership.eligible
                    and (r.membership.event_type, r.membership.currency)
                    == (group.event_type, group.currency)
                    for r in actual
                )
            indexed = {(*series_key(p), p.business_date): p for p in points}
            expected = [
                pair for s in plan.scopes for pair in requested(plan.native_protocol(s), points)
            ]
            assert len(actual) == len(expected) == plan.rows
            assert [r.membership for r in actual] == [m for m, _ in expected]
            for value, (member, p) in zip(actual, expected, strict=True):
                if member.eligible and member.role in ("train", "validation"):
                    assert value.training_or_validation_row == count_rate_row(p, indexed)
                else:
                    assert value.training_or_validation_row is None
            for changed in (
                plan.model_copy(update={"native_points_sha256": "0" * 64}),
                plan.model_copy(update={"feature_plan_sha256": "0" * 64}),
            ):
                with pytest.raises(ValueError, match="complete_feature_parent_binding"):
                    list(adapter.training_memberships(changed))
            with pytest.raises(ValueError, match="complete_scope_inventory"):
                list(
                    adapter.training_memberships(
                        plan.model_copy(update={"scopes": plan.scopes[:-1]})
                    )
                )
        assert adapter.receipt()["complete_native_point_census_passed"]
        assert not adapter.receipt()["quality_qualified"]
        with pytest.raises(RuntimeError, match="state_unavailable"):
            list(adapter.training_memberships(plan))
    assert not list(tmp_path.iterdir())
