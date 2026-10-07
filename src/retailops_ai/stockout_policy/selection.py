"""A threshold proposal for a later-calibrated model, assessed on development CAL."""

from pathlib import Path
from typing import Any

from retailops_ai.source_snapshot.files import canonical_json, read_json
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_policy.assessment import assess
from retailops_ai.stockout_policy.bundle import implementation
from retailops_ai.stockout_policy.contract import PolicyRow, PolicySpec
from retailops_ai.stockout_qualification.bundle import membership
from retailops_ai.stockout_selection.bundle import development_source_parameters, seal_selection
from retailops_ai.stockout_selection.contract import (
    ConditionalRiskPipeline,
    SelectionModelPin,
    SelectionPolicy,
)
from retailops_ai.stockout_selection.fit import fit_selection
from retailops_ai.stockout_selection.pipeline import predict_conditional
from retailops_ai.stockout_temporal_series.bundle import assemble_partitioned_development
from retailops_ai.stockout_temporal_storage.store import PartitionInputs, capture
from retailops_ai.stockout_training.inputs import keys_sha256, labels_sha256


def build_selected_proposal(
    target: Path,
    inputs: PartitionInputs,
    policy: SelectionPolicy,
    spec: PolicySpec,
    *,
    allow_evaluation_truth: bool = False,
) -> dict[str, Any]:
    if not allow_evaluation_truth:
        raise ValueError("stockout_policy_private_verification_opt_in_required")
    spec = PolicySpec.model_validate_json(spec.model_dump_json())
    if spec.version != "stockout-threshold-proposal-2.0.0":
        raise ValueError("stockout_policy_selection_protocol_required")
    roots = {**inputs.roots(), "temporal": target}
    before = {name: capture(root) for name, root in roots.items()}
    data = assemble_partitioned_development(target, inputs, allow_evaluation_truth=True)
    document = read_json(target, "manifest.json")
    if document["temporal_bundle_id"] != data.parents["temporal_bundle_id"]:
        raise ValueError("stockout_policy_temporal_parent")
    selection = fit_selection(data, membership(target, document), policy)
    seal_selection(selection, development_source_parameters(inputs.curated, policy))
    sid = selection["selection_id"]
    content = selection["content"]
    pipeline = ConditionalRiskPipeline.model_validate_json(
        canonical_json(content["selected_pipeline"])
    )
    rows, ys = data.rows["calibration"], data.outcomes["calibration"]
    ps = predict_conditional(pipeline, rows).tolist()
    assessment_rows = [
        PolicyRow(
            product_id=r["product_id"],
            stock_location_id=r["stock_location_id"],
            as_of=r["as_of"],
            category_id=r["category_id"],
            probability=p,
            incident_stockout=y,
            historical_inventory_constraint=r["values"]["history_constrained_days"] > 0,
        )
        for r, y, p in zip(rows, ys, ps, strict=True)
    ]
    pin = SelectionModelPin(
        selection_id=sid,
        model_id=content["selected_model_id"],
        calibrator_sha256=content["selected_calibrator_sha256"],
        selection_known_at=content["selection_known_at"],
    )
    result = dict(
        assessment=assess(assessment_rows, spec),
        selected_model=content["selected"],
        selected_pipeline=pipeline.model_dump(mode="json"),
        development_selection_gates=content["selected_development_gates"],
        metrics_interpretation="development_selection_not_independent_quality",
        independent_quality_accepted=False,
        development_rows=len(rows),
        development_keys_sha256=keys_sha256(rows),
        development_labels_sha256=labels_sha256(rows, ys),
        thresholds_approved=False,
        final_test_outcomes_evaluated=False,
        model_promoted=False,
        serving_eligible=False,
    )
    after = {name: capture(root) for name, root in roots.items()}
    if before != after:
        raise ValueError("stockout_policy_source_changed")
    descriptor = dict(
        role="stockout_threshold_policy_proposal",
        schema_version="2.0.0",
        parents=data.parents,
        model_pin=pin.model_dump(mode="json"),
        policy=spec.model_dump(mode="json"),
        implementation=implementation(),
        parent_seal_sha256=digest(before),
        content_sha256=digest(result),
    )
    output = dict(
        proposal_id="stockout-policy-proposal-sha256-" + digest(descriptor),
        descriptor=descriptor,
        content=result,
    )
    if len(canonical_json(output)) > 1024**2:
        raise ValueError("stockout_policy_proposal_byte_limit")
    return output
