"""Capacity on the entire sealed physical input profile, before any authorized projection."""

import math

from retailops_ai.domain.access import Principal
from retailops_ai.stockout_jobs.batch import StockoutOutput
from retailops_ai.stockout_jobs.read_contracts import StockoutPriority
from retailops_ai.stockout_runtime.inputs import PreparedStockoutInputs


def priorities(
    output: StockoutOutput, inputs: PreparedStockoutInputs, principal: Principal
) -> dict[str, StockoutPriority]:
    output = StockoutOutput.model_validate_json(output.model_dump_json())
    inputs = PreparedStockoutInputs.model_validate_json(inputs.model_dump_json())
    if output.profile_id != inputs.inputs_id or {
        (r.product_id, r.stock_location_id, r.as_of) for r in output.items
    } != {
        (r.feature.product_id, r.feature.stock_location_id, r.feature.as_of) for r in inputs.points
    }:
        raise ValueError("stockout_priority_complete_profile_required")
    eligible = [r for r in output.items if r.status == "scored" and r.probability is not None]
    capacity = output.release.binding.approval.qualification.policy.spec.capacity
    slots = min(
        len(eligible),
        int(capacity.top_n or 0)
        if capacity.mode == "top_n"
        else math.ceil(len(eligible) * float(capacity.fraction or 0)),
    )
    ranked = sorted(
        eligible, key=lambda r: (-float(r.probability or 0), r.product_id, r.stock_location_id)
    )
    rank = {r.risk_id: i + 1 for i, r in enumerate(ranked)}
    access = principal.stockout
    complete_access = (
        "stockout:read" in principal.capabilities
        and access is not None
        and set(inputs.scope.product_ids) <= access.product_ids
        and set(inputs.scope.stock_location_ids) <= access.stock_location_ids
    )
    return {
        r.risk_id: StockoutPriority(
            universe_id=inputs.inputs_id,
            selected_at_origin=rank[r.risk_id] <= slots if r.risk_id in rank else None,
            rank_at_origin=rank.get(r.risk_id) if complete_access else None,
            eligible_in_universe=len(eligible) if complete_access else None,
            capacity_slots=slots if complete_access else None,
            other_scope_context="visible_authorized" if complete_access else "withheld",
        )
        for r in output.items
    }
