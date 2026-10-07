"""Replay original development before constructing an unapproved policy proposal."""

import hashlib
from importlib.resources import files
from pathlib import Path
from typing import Any

from retailops_ai.source_snapshot.files import canonical_json, read_json
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_policy.assessment import assess
from retailops_ai.stockout_policy.contract import ModelPolicyPin, PolicyRow, PolicySpec
from retailops_ai.stockout_qualification.bundle import (
    implementation as qualification_implementation,
)
from retailops_ai.stockout_qualification.bundle import membership
from retailops_ai.stockout_qualification.contract import QualificationPolicy
from retailops_ai.stockout_qualification.fit import fit_qualification
from retailops_ai.stockout_selection.bundle import implementation as selection_implementation
from retailops_ai.stockout_temporal_series.bundle import assemble_partitioned_development
from retailops_ai.stockout_temporal_storage.store import PartitionInputs, capture
from retailops_ai.stockout_training.contract import RiskPipeline
from retailops_ai.stockout_training.inputs import keys_sha256, labels_sha256
from retailops_ai.stockout_training.pipeline import predict


def implementation() -> dict[str, Any]:
    code = {
        r.name: hashlib.sha256(r.read_bytes()).hexdigest()
        for r in files("retailops_ai.stockout_policy").iterdir()
        if r.is_file() and r.name.endswith(".py")
    }
    return dict(
        code=code,
        code_sha256=digest(code),
        qualification=qualification_implementation(),
        selection=selection_implementation(),
    )


def build_proposal(
    target: Path,
    inputs: PartitionInputs,
    qualification_policy: QualificationPolicy,
    spec: PolicySpec,
    *,
    allow_evaluation_truth: bool = False,
) -> dict[str, Any]:
    if not allow_evaluation_truth:
        raise ValueError("stockout_policy_private_verification_opt_in_required")
    spec = PolicySpec.model_validate_json(spec.model_dump_json())
    if spec.version != "stockout-threshold-proposal-1.0.0":
        raise ValueError("stockout_policy_independent_protocol_required")
    roots = {**inputs.roots(), "temporal": target}
    before = {name: capture(root) for name, root in roots.items()}
    data = assemble_partitioned_development(target, inputs, allow_evaluation_truth=True)
    document = read_json(target, "manifest.json")
    if document["temporal_bundle_id"] != data.parents["temporal_bundle_id"]:
        raise ValueError("stockout_policy_temporal_parent")
    q = fit_qualification(data, membership(target, document), qualification_policy)
    q["descriptor"]["implementation"] = qualification_implementation()
    qid = "stockout-qualification-sha256-" + digest(q["descriptor"])
    content = q["content"]
    pipeline = RiskPipeline.model_validate_json(
        canonical_json(content["pipelines"][content["selected_on_raw_tune"]])
    )
    if pipeline.sigmoid is None or pipeline.sigmoid.slope <= 0:
        raise ValueError("stockout_policy_monotone_calibrator_required")
    rows = data.rows["tune"]
    ys = data.outcomes["tune"]
    ps = predict(pipeline, rows)
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
    pin = ModelPolicyPin(
        qualification_id=qid,
        model_id="risk-model-sha256-"
        + digest(content["pipelines"][content["selected_on_raw_tune"]]),
        calibrator_sha256=digest(pipeline.sigmoid.model_dump(mode="json")),
        selection_known_at=content["selection_known_at"],
    )
    result = dict(
        assessment=assess(assessment_rows, spec),
        selected_model=content["selected_on_raw_tune"],
        selected_pipeline=pipeline.model_dump(mode="json"),
        independent_development_quality_status=content["gates"]["status"],
        independent_development_gates=content["gates"],
        tune_rows=len(rows),
        tune_keys_sha256=keys_sha256(rows),
        tune_labels_sha256=labels_sha256(rows, ys),
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
        schema_version="1.0.0",
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
