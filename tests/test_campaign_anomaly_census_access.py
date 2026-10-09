"""Real public Source -> full native features -> native census scoring controls.

Already exposed Source fixtures and a genuine small controlled forest are used
here. This is no Project fit, truth read, final access or scientific admission.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from test_anomaly_portfolio_model import saved_model  # noqa: F401
from test_campaign_anomaly_days import day_case  # noqa: F401
from test_campaign_anomaly_features import feature_plan, open_gate
from test_campaign_anomaly_parent import public_parent  # noqa: F401

from retailops_ai.anomaly_detectors.protocol import Scope, Window, series_key
from retailops_ai.anomaly_evaluation.verification import verify_scores
from retailops_ai.anomaly_portfolio.model import Descriptor, Model, score
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_anomaly_features import (
    CampaignAnomalyFeatureProjection,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_scoring import iter_anomaly_census_scores


def test_public_native_window_history_missing_scope_and_complete_score_replay(
    day_case,  # noqa: F811 - imported parametrized fixture
    saved_model,  # noqa: F811 - imported fixture
    tmp_path,
):
    # The controlled forest has no Source outcomes. Its independent control
    # clock precedes the exposed July fixtures; identity is recomputed rather
    # than editing a saved model in place or claiming a Project fit.
    descriptor = Descriptor.model_validate_json(
        canonical_bytes(
            {
                **saved_model.descriptor.model_dump(mode="json"),
                "train": {"start": "2026-06-01", "end": "2026-06-20"},
                "validation": {"start": "2026-06-23", "end": "2026-06-26"},
                "training_cutoff": "2026-06-22T00:00:00Z",
                "selection_cutoff": "2026-06-30T00:00:00Z",
            }
        )
    )
    model = Model(
        detector_id="anomaly-detector-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    )
    with open_gate(day_case, tmp_path) as (gate, _, _):
        adapter = CampaignAnomalyFeatureProjection(gate, feature_plan(gate), tmp_path)
        with adapter:
            full = list(adapter.points())
            real = {
                series_key(p): Scope.model_validate(
                    {name: getattr(p, name) for name in Scope.model_fields}
                )
                for p in full
            }
            missing = next(iter(real.values())).model_copy(
                update={"product_id": str(UUID(int=999))}
            )
            scopes = tuple(sorted((*real.values(), missing), key=series_key))
            window = Window(start=date(2026, 7, 25), end=date(2026, 7, 31))
            as_of = datetime(2026, 8, 7, tzinfo=UTC)
            earliest = window.start - timedelta(days=6)
            expected_points = [p for p in full if earliest <= p.business_date <= window.end]
            assert list(adapter.scoring_points(scopes, window)) == expected_points
            assert all(series_key(p) != series_key(missing) for p in expected_points)
            # A narrower role preserves the same exact features and preceding
            # dates, including the boundary exactly six days before its start.
            narrow = Window(start=window.end, end=window.end)
            assert list(adapter.scoring_points(scopes, narrow)) == [
                p for p in full if narrow.start - timedelta(days=6) <= p.business_date <= narrow.end
            ]
            for family in ("seasonal_residual", "isolation_forest"):
                expected = score(model, expected_points, scopes, window, family, "batch", as_of)
                rows = list(
                    iter_anomaly_census_scores(
                        model,
                        adapter.scoring_points(scopes, window),
                        scopes,
                        window,
                        family,
                        "batch",
                        as_of,
                        batch_points=26,
                    )
                )
                assert [r.decision for r in rows] == expected
                assert len(rows) == len(scopes) * 7
                absent = [r for r in rows if series_key(r.decision) == series_key(missing)]
                assert len(absent) == 7
                assert all(
                    r.decision.status == "insufficient_data"
                    and r.decision.score is None
                    and r.decision.alert is None
                    and r.model_row is None
                    and r.public_point_sha256 is None
                    for r in absent
                )
                verify_scores(
                    model,
                    [r.decision for r in rows],
                    [r.model_row.model_dump(mode="json") if r.model_row else None for r in rows],
                )
            for invalid in ((), scopes[::-1], (scopes[0], scopes[0])):
                with pytest.raises(ValueError, match="features_scoring_scope"):
                    list(adapter.scoring_points(invalid, window))
            with pytest.raises(ValueError, match="features_scoring_scope_or_window"):
                list(
                    adapter.scoring_points(
                        scopes,
                        Window(
                            start=date(2000, 1, 1),
                            end=date(2006, 1, 1),
                        ),
                    )
                )
        assert adapter.receipt()["complete_native_point_census_passed"]
        assert not adapter.receipt()["quality_qualified"]
        with pytest.raises(RuntimeError, match="state_unavailable"):
            list(adapter.scoring_points(scopes, window))
    assert not list(tmp_path.iterdir())
