"""Public full-parent replay and immutable development selection capsule."""

import hashlib
from importlib.resources import files
from pathlib import Path
from typing import Any

from retailops_ai.source_snapshot.files import canonical_json, read_json
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_explanation.diagnostics import coefficients
from retailops_ai.stockout_qualification.bundle import (
    factual_context,
    membership,
)
from retailops_ai.stockout_qualification.bundle import (
    implementation as qualification_implementation,
)
from retailops_ai.stockout_selection.contract import SelectionPolicy
from retailops_ai.stockout_selection.fit import fit_selection
from retailops_ai.stockout_temporal_series.bundle import assemble_partitioned_development
from retailops_ai.stockout_temporal_storage.store import PartitionInputs, capture
from retailops_ai.stockout_training.contract import MAX_BYTES, RiskPipeline


def implementation() -> dict[str, Any]:
    code = {
        r.name: hashlib.sha256(r.read_bytes()).hexdigest()
        for r in sorted(files("retailops_ai.stockout_selection").iterdir(), key=lambda r: r.name)
        if r.is_file() and r.name.endswith(".py")
    }
    return dict(code=code, code_sha256=digest(code), dependencies=qualification_implementation())


def development_source_parameters(curated: Path, policy: SelectionPolicy) -> dict[str, Any]:
    # Call only after actual curated/temporal replay; the config is then bound by parent IDs.
    params = read_json(curated, "curated_manifest.json")["descriptor"]["source_parameters"]
    if type(params.get("seed")) is not int or params["seed"] != policy.development_seed:
        raise ValueError("stockout_selection_actual_development_seed")
    return dict(params)


def seal_selection(selection: dict[str, Any], source_parameters: dict[str, Any]) -> None:
    selection["descriptor"]["implementation"] = implementation()
    selection["descriptor"]["actual_development_source_parameters"] = source_parameters
    selection["selection_id"] = "stockout-selection-sha256-" + digest(selection["descriptor"])


def build_selection(
    target: Path,
    inputs: PartitionInputs,
    policy: SelectionPolicy,
    *,
    allow_evaluation_truth: bool = False,
) -> dict[str, Any]:
    if not allow_evaluation_truth:
        raise ValueError("stockout_selection_private_verification_opt_in_required")
    roots = {**inputs.roots(), "temporal": target}
    before = {name: capture(root) for name, root in roots.items()}
    data = assemble_partitioned_development(target, inputs, allow_evaluation_truth=True)
    document = read_json(target, "manifest.json")
    if document["temporal_bundle_id"] != data.parents["temporal_bundle_id"]:
        raise ValueError("stockout_selection_actual_temporal_parent")
    selection = fit_selection(data, membership(target, document), policy)
    seal_selection(selection, development_source_parameters(inputs.curated, policy))
    content = selection["content"]
    selected = content["selected"]
    base_name = content["comparison"][selected]["base_model"]
    base = RiskPipeline.model_validate_json(canonical_json(content["base_pipelines"][base_name]))
    card_content = dict(
        selection_id=selection["selection_id"],
        parents=data.parents,
        selected=selected,
        selected_model_id=content["selected_model_id"],
        protocol=selection["descriptor"]["policy"],
        roles=content["roles"],
        selection_known_at=content["selection_known_at"],
        selection_diagnostics=content["comparison"][selected],
        base_coefficients=coefficients(base) if base.family == "logistic_regression" else None,
        complete_calibrator=content["selected_pipeline"]["calibrator"],
        local_factual_context=[
            {**factual_context(r), "role": "development_selection"}
            for r in data.rows["calibration"]
        ],
        limitations=content["limitations"],
        readiness=dict(
            independent_quality_accepted=False,
            final_test_outcomes_evaluated=False,
            threshold_policy_approved=False,
            model_ready=False,
            production_promoted=False,
        ),
    )
    card_descriptor = dict(
        schema_version="stockout-selection-model-card-3.0.0",
        parents=data.parents,
        selection_id=selection["selection_id"],
        content_sha256=digest(card_content),
        implementation=implementation(),
    )
    after = {name: capture(root) for name, root in roots.items()}
    if before != after:
        raise ValueError("stockout_selection_parent_changed_during_build")
    result = dict(
        schema_version="stockout-selection-capsule-2.0.0",
        selection=selection,
        model_card=dict(
            card_id="stockout-card-sha256-" + digest(card_descriptor),
            descriptor=card_descriptor,
            content=card_content,
        ),
        parents_full_replay=True,
        parent_seal_sha256=digest(before),
        independent_quality_accepted=False,
        final_test_outcomes_evaluated=False,
        model_promoted=False,
        ai08_ready=False,
    )
    if len(canonical_json(result)) + 1 > MAX_BYTES:
        raise ValueError("stockout_selection_output_byte_limit")
    return result
