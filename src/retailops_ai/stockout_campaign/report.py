"""All six frozen world receipts are required; incomplete or failed quality stays visible."""

from typing import Any

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.stockout_campaign.contract import (
    CampaignFreeze,
    CampaignPermission,
    require_permission,
)
from retailops_ai.stockout_campaign.implementation import code_digest, lock_digest


def aggregate(
    reports: list[dict[str, Any]],
    *,
    freeze: CampaignFreeze,
    permission: CampaignPermission | None,
) -> dict[str, Any]:
    require_permission(freeze, permission)
    if permission is None:
        raise ValueError("stockout_final_owner_permission_required")
    if len(reports) != 6:
        raise ValueError("stockout_final_six_complete_worlds_required")
    found = {(r["world"], r["seed"]) for r in reports}
    if found != {(s.world, s.seed) for s in freeze.sources}:
        raise ValueError("stockout_final_duplicate_or_missing_world")
    permission_sha = canonical_sha256(permission.model_dump(mode="json"))
    if (
        code_digest() != freeze.evaluator_code_sha256
        or lock_digest() != freeze.dependency_lock_sha256
    ):
        raise ValueError("stockout_final_execution_code_changed")
    blockers = []
    for source in freeze.sources:
        report = next(r for r in reports if (r["world"], r["seed"]) == (source.world, source.seed))
        if (
            report["schema_version"] != "stockout-final-world-report-1.0.0"
            or report["campaign_id"] != freeze.campaign_id
            or report["source"] != source.model_dump(mode="json")
            or report["selection_id"] != freeze.selection_id
            or report["recipe_content_sha256"] != freeze.recipe_content_sha256
            or report["policy_content_sha256"] != freeze.policy_content_sha256
            or report["execution_code_sha256"] != freeze.evaluator_code_sha256
            or report["dependency_lock_sha256"] != freeze.dependency_lock_sha256
            or report["permission_sha256"] != permission_sha
            or report["coverage"]["eligible"] != source.eligible_test_membership
            or report["metrics"]["all"]["rows"] != source.eligible_test_membership
            or report["model_refits"] != 0
            or report["recalibration"] is not False
            or report["thresholds_changed"] is not False
            or report["source_generation"] is not False
            or report["final_test_outcomes_evaluated"] is not True
            or report["model_promoted"] is not False
            or report["ai08_ready"] is not False
        ):
            raise ValueError("stockout_final_world_receipt_binding")
        required = {
            "all",
            "historical_inventory_constraint:true",
            *("category:" + v for v in freeze.expected_categories),
            *("stock_location:" + v for v in freeze.expected_stock_locations),
        }
        scenarios = (
            {
                "normal",
                "promotion",
                "demand_shock",
                "inventory_constraint",
                "control:promotion",
                "control:demand_shock",
                "control:inventory_constraint",
            }
            if source.world == "future_stress"
            else set()
        )
        if (
            set(report["segment_gates"]["segments"]) != required
            or set(report["scenario_gates"]) != scenarios
        ):
            raise ValueError("stockout_final_required_gate_missing")
        failures = [
            name
            for name, gate in report["segment_gates"]["segments"].items()
            if gate["status"] != "passed"
        ]
        failures.extend(
            "scenario:" + name
            for name, gate in report["scenario_gates"].items()
            if gate["status"] != "passed"
        )
        passed = (
            report["segment_gates"]["status"] == "passed"
            and report["segment_gates"]["required_segment_universe_complete"] is True
            and not failures
        )
        if (
            passed != (report["status"] == "passed")
            or passed != report["independent_quality_accepted"]
        ):
            raise ValueError("stockout_final_world_status_disagrees_with_gates")
        if not passed:
            blockers.append(
                dict(world=source.world, seed=source.seed, failed_or_not_evaluable=failures)
            )
    content = dict(
        schema_version="stockout-final-quality-1.0.0",
        campaign_id=freeze.campaign_id,
        freeze_content_sha256=canonical_sha256(freeze.model_dump(mode="json")),
        permission_sha256=permission_sha,
        worlds=sorted(reports, key=lambda r: (r["world"], r["seed"])),
        blockers=blockers,
        status="passed" if not blockers else "not_ready",
        independent_quality_accepted=not blockers,
        final_test_outcomes_evaluated=True,
        thresholds_approved=True,
        model_refits=0,
        row_counts_by_world={
            s.world + ":" + str(s.seed): s.eligible_test_membership for s in freeze.sources
        },
        no_pooled_quality_claim=True,
        model_promoted=False,
        ai08_ready=False,
    )
    return dict(
        quality_id="stockout-final-quality-sha256-" + canonical_sha256(content), content=content
    )
