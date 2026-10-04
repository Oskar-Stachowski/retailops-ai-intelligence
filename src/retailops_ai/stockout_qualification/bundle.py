"""Public qualification/card builder: replay first, then return a fully sealed result."""

import hashlib
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from retailops_ai.source_snapshot.files import canonical_json, read_bytes, read_json
from retailops_ai.stockout.feature_contract import DEFAULT_FEATURE_POLICY, FeatureValues
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_explanation.card import explanation_implementation
from retailops_ai.stockout_explanation.diagnostics import (
    MAX_CARD_BYTES,
    ExplanationPolicy,
    coefficients,
    permutation_importance,
)
from retailops_ai.stockout_qualification.contract import QualificationPolicy
from retailops_ai.stockout_qualification.fit import fit_qualification
from retailops_ai.stockout_temporal_series.bundle import assemble_partitioned_development
from retailops_ai.stockout_temporal_storage.store import PartitionInputs, capture
from retailops_ai.stockout_training.contract import MAX_BYTES, RiskPipeline
from retailops_ai.stockout_training.inputs import DevelopmentData


def implementation() -> dict[str, Any]:
    code = {
        r.name: hashlib.sha256(r.read_bytes()).hexdigest()
        for r in sorted(
            files("retailops_ai.stockout_qualification").iterdir(), key=lambda r: r.name
        )
        if r.is_file() and r.name.endswith(".py")
    }
    return dict(code=code, code_sha256=digest(code), dependencies=explanation_implementation())


def membership(target: Path, document: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for spec in document["descriptor"]["partitions"]:
        for ref in spec["files"]:
            if ref["role"] != "membership":
                continue
            raw = read_bytes(target, ref["path"], 16 * 1024**2)
            if len(raw) != ref["bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
                raise ValueError("stockout_qualification_membership_file_checksum")
            for row in pq.read_table(pa.BufferReader(raw)).to_pylist():
                if row["role"] in ("train", "tune", "calibration"):
                    rows.append(
                        {
                            k: v.isoformat().replace("+00:00", "Z")
                            if isinstance(v, datetime)
                            else v
                            for k, v in row.items()
                        }
                    )
    if len(rows) > 10000:
        raise ValueError("stockout_qualification_membership_row_limit")
    return rows


def factual_context(row: dict[str, Any]) -> dict[str, Any]:
    """This row is internal, obtained only after full parent replay and eligibility."""
    values = FeatureValues.model_validate({k: row["values"][k] for k in FeatureValues.model_fields})
    if (
        values.available_qty is None
        or values.available_qty <= 0
        or values.snapshot_age_hours is None
        or values.snapshot_age_hours * 3600 > DEFAULT_FEATURE_POLICY.max_snapshot_age_seconds
        or values.history_known_days < DEFAULT_FEATURE_POLICY.minimum_known_days
    ):
        raise ValueError("stockout_qualification_context_requires_fresh_eligible_inventory")
    facts = [
        dict(code="known_available_stock", field="available_qty", value=values.available_qty),
        dict(
            code="inventory_snapshot_age",
            field="snapshot_age_hours",
            value=values.snapshot_age_hours,
        ),
    ]
    for field, code in (
        ("days_of_supply_observed", "supply_from_observed_sales"),
        ("days_of_supply_in_stock", "supply_from_verified_in_stock_sales"),
        ("due_within_7d_quantity", "known_delivery_plan_within_horizon"),
        ("overdue_order_quantity", "known_overdue_order_quantity"),
        ("history_constrained_days", "history_had_inventory_constraints"),
        ("history_inventory_unknown_days", "history_inventory_coverage_missing"),
    ):
        value = getattr(values, field)
        if value is not None and (field.startswith("days_of_supply") or value > 0):
            facts.append(dict(code=code, field=field, value=value))
    return dict(
        product_id=row["product_id"],
        stock_location_id=row["stock_location_id"],
        as_of=row["as_of"],
        role="tune",
        facts=facts,
        interpretation="verified_PIT_context_not_local_model_attribution",
    )


def card(data: DevelopmentData, qualification: dict[str, Any]) -> dict[str, Any]:
    content = qualification["content"]
    pipelines = {
        n: RiskPipeline.model_validate_json(canonical_json(p))
        for n, p in content["pipelines"].items()
    }
    selected = content["selected_on_raw_tune"]
    body = dict(
        intended_use="bounded_synthetic_independent_development_review",
        parents=data.parents,
        qualification_id=qualification["qualification_id"],
        selected_model=selected,
        selected_model_id="risk-model-sha256-" + digest(content["pipelines"][selected]),
        qualification_policy=qualification["descriptor"]["policy"],
        roles=content["roles"],
        selection_known_at=content["selection_known_at"],
        independent_validation=content["independent_validation"][selected],
        quality_gates=content["gates"],
        coefficient_tables={
            n: coefficients(p) for n, p in pipelines.items() if p.family == "logistic_regression"
        },
        tune_permutation_importance=permutation_importance(
            pipelines[selected], data.rows["tune"], data.outcomes["tune"], ExplanationPolicy()
        ),
        local_factual_context=[factual_context(row) for row in data.rows["tune"]],
        limitations=content["limitations"],
        readiness=dict(
            partition_parent_card_complete=True,
            calibration_evaluation_out_of_sample=True,
            final_test_outcomes_evaluated=False,
            threshold_policy_ready=False,
            model_ready=False,
            production_promoted=False,
        ),
    )
    descriptor = dict(
        schema_version="2.0.0",
        role="stockout_partition_qualification_model_card",
        parents={**data.parents, "qualification_id": qualification["qualification_id"]},
        selected_model_id=body["selected_model_id"],
        content_sha256=digest(body),
        implementation=implementation(),
    )
    document = dict(
        card_id="stockout-card-sha256-" + digest(descriptor), descriptor=descriptor, content=body
    )
    if len(canonical_json(document)) + 1 > MAX_CARD_BYTES:
        raise ValueError("stockout_qualification_card_byte_limit")
    return document


def build_qualification(
    target: Path,
    inputs: PartitionInputs,
    policy: QualificationPolicy,
    *,
    allow_evaluation_truth: bool = False,
) -> dict[str, Any]:
    if not allow_evaluation_truth:
        raise ValueError("stockout_qualification_private_verification_opt_in_required")
    roots = {**inputs.roots(), "temporal": target}
    before = {name: capture(root) for name, root in roots.items()}
    data = assemble_partitioned_development(
        target, inputs, allow_evaluation_truth=allow_evaluation_truth
    )
    document = read_json(target, "manifest.json")
    if document["temporal_bundle_id"] != data.parents["temporal_bundle_id"]:
        raise ValueError("stockout_qualification_actual_temporal_parent")
    qualification = fit_qualification(data, membership(target, document), policy)
    qualification["descriptor"]["implementation"] = implementation()
    qualification["qualification_id"] = "stockout-qualification-sha256-" + digest(
        qualification["descriptor"]
    )
    model_card = card(data, qualification)
    after = {name: capture(root) for name, root in roots.items()}
    if before != after:
        raise ValueError("stockout_qualification_parent_changed_during_build")
    result = dict(
        schema_version="stockout-qualification-capsule-1.0.0",
        qualification=qualification,
        model_card=model_card,
        parents_full_replay=True,
        parent_seal_sha256=digest(before),
        final_test_outcomes_evaluated=False,
        model_promoted=False,
        ai08_ready=False,
    )
    if len(canonical_json(result)) + 1 > MAX_BYTES:
        raise ValueError("stockout_qualification_output_byte_limit")
    return result
