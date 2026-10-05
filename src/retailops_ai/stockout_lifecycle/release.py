"""Verify complete private stockout approval bytes and replay the portable smoke result."""

import hashlib
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from retailops_ai.data_contracts.common import Contract, FalseFlag, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.model_lifecycle.contracts import Receipt
from retailops_ai.source_snapshot.files import inventory, read_bytes, read_json, regular_file
from retailops_ai.stockout_campaign.contract import CampaignFreeze, CampaignPermission
from retailops_ai.stockout_campaign.evaluation import bound_recipes
from retailops_ai.stockout_campaign.report import aggregate
from retailops_ai.stockout_lifecycle.contract import (
    REVIEW_GATES,
    StockoutApproval,
    StockoutQualification,
    capsule_names,
)
from retailops_ai.stockout_runtime.contracts import (
    RiskItem,
    RuntimeReleasePin,
    ScoringPolicy,
    ScoringRecipe,
)
from retailops_ai.stockout_runtime.inputs import PreparedStockoutInputs
from retailops_ai.stockout_runtime.scoring import score_point

MAX_CAPSULE_BYTES = 16 * 1024**2


def receipt(raw: bytes) -> Receipt:
    return Receipt(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


class ServingSmoke(Contract):
    version: Literal["stockout-serving-smoke-1.0.0"] = "stockout-serving-smoke-1.0.0"
    purpose: Literal["load_predict_smoke_not_publication"] = "load_predict_smoke_not_publication"
    inputs_id: str
    recipe_content_sha256: str
    policy_content_sha256: str
    generated_at: UtcTime
    items: tuple[RiskItem, ...] = Field(min_length=1, max_length=100)
    model_refits: Annotated[int, Field(ge=0, le=0)] = 0
    serving_eligible: FalseFlag = False


def signature(recipe: ScoringRecipe, policy: ScoringPolicy) -> dict[str, Any]:
    """Only verified public input points are accepted; targets are outside this contract."""
    if recipe.pin != policy.pin:
        raise ValueError("stockout_signature_model_policy_pin")
    inputs = PreparedStockoutInputs.model_json_schema()
    output = RiskItem.model_json_schema()
    return dict(
        version="stockout-serving-signature-1.0.0",
        recipe_content_sha256=canonical_sha256(recipe.model_dump(mode="json")),
        policy_content_sha256=canonical_sha256(policy.model_dump(mode="json")),
        input_schema=inputs,
        output_schema=output,
        input_schema_sha256=canonical_sha256(inputs),
        output_schema_sha256=canonical_sha256(output),
        physical_key=["product_id", "stock_location_id", "as_of"],
        arbitrary_feature_upload=False,
        private_truth_input=False,
        model_refits=0,
    )


def predict_smoke(
    inputs: PreparedStockoutInputs,
    recipe: ScoringRecipe,
    policy: ScoringPolicy,
    *,
    generated_at: datetime,
) -> ServingSmoke:
    """A provisional test pin deliberately cannot stand in for a production release."""
    inputs = PreparedStockoutInputs.model_validate_json(inputs.model_dump_json())
    recipe = ScoringRecipe.model_validate_json(recipe.model_dump_json())
    policy = ScoringPolicy.model_validate_json(policy.model_dump_json())
    if recipe.pin != policy.pin or inputs.as_of < recipe.pin.selection_known_at:
        raise ValueError("stockout_smoke_model_policy_or_chronology")
    pin = RuntimeReleasePin(
        model_name="retailops-stockout-risk-test-mechanics",
        model_version="1",
        release_id="stockout-release-sha256-" + "0" * 64,
        image_digest="sha256:" + "0" * 64,
        recipe_content_sha256=canonical_sha256(recipe.model_dump(mode="json")),
        policy_content_sha256=canonical_sha256(policy.model_dump(mode="json")),
    )
    rows = tuple(
        score_point(
            point.feature,
            category_id=point.category_id,
            category_available_at=point.category_available_at,
            upstream=point.upstream,
            recipe=recipe,
            policy=policy,
            release=pin,
            lineage=inputs.lineage,
            run_id="run-" + "0" * 32,
            generated_at=generated_at,
        )
        for point in inputs.points
    )
    return ServingSmoke(
        inputs_id=inputs.inputs_id,
        recipe_content_sha256=pin.recipe_content_sha256,
        policy_content_sha256=pin.policy_content_sha256,
        generated_at=generated_at,
        items=rows,
    )


def verify_smoke(reference: ServingSmoke, replayed: ServingSmoke) -> dict[str, Any]:
    """Exact structure/decisions; probability roundoff uses the portable 1e-12 floor."""
    reference = ServingSmoke.model_validate_json(reference.model_dump_json())
    replayed = ServingSmoke.model_validate_json(replayed.model_dump_json())
    if reference.model_dump(exclude={"items"}) != replayed.model_dump(exclude={"items"}) or len(
        reference.items
    ) != len(replayed.items):
        raise ValueError("stockout_capsule_smoke_non_probability_changed")
    maximum = 0.0
    for original, current in zip(reference.items, replayed.items, strict=True):
        if original.model_dump(exclude={"probability"}) != current.model_dump(
            exclude={"probability"}
        ):
            raise ValueError("stockout_capsule_smoke_non_probability_changed")
        if original.probability is None or current.probability is None:
            if original.probability != current.probability:
                raise ValueError("stockout_capsule_smoke_probability_changed")
        else:
            difference = abs(original.probability - current.probability)
            if difference > 1e-12:
                raise ValueError("stockout_capsule_smoke_probability_changed")
            maximum = max(maximum, difference)
    return dict(
        maximum_absolute_probability_difference=maximum,
        absolute_tolerance=1e-12,
        non_probability_fields_exact=True,
    )


def _private(root: Path, names: set[str]) -> dict[str, bytes]:
    inventory(root, names)
    if root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
        raise ValueError("stockout_private_capsule_required")
    raw = {}
    total = 0
    for name in sorted(names):
        with regular_file(root, name) as stream:
            info = os.fstat(stream.fileno())
            if (
                info.st_uid != os.geteuid()
                or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_size > MAX_CAPSULE_BYTES
            ):
                raise ValueError("stockout_private_capsule_required")
        raw[name] = read_bytes(root, name, limit=MAX_CAPSULE_BYTES)
        total += len(raw[name])
        if total > MAX_CAPSULE_BYTES:
            raise ValueError("stockout_capsule_total_byte_limit")
    return raw


def verify_approved_capsule(
    root: Path, *, approval_id: str, current: bool = True
) -> StockoutApproval:
    """Bind every gate and inference byte; genuine final acceptance requires all six worlds."""
    approval = StockoutApproval.model_validate_json(read_bytes(root, "approval.json"))
    final = approval.qualification.purpose == "qualified_stockout"
    raw = _private(root, capsule_names(final=final))
    if StockoutApproval.model_validate_json(raw["approval.json"]) != approval:
        raise ValueError("stockout_capsule_changed_during_read")
    q = approval.qualification
    if approval.release_id != approval_id:
        raise ValueError("stockout_capsule_approval_pin")
    if current and not approval.reviewed_at <= datetime.now(UTC) < q.valid_until:
        raise ValueError("stockout_capsule_approval_expired_or_future")
    if StockoutQualification.model_validate_json(raw["qualification.json"]) != q:
        raise ValueError("stockout_capsule_qualification_changed")
    recipe = ScoringRecipe.model_validate_json(raw["recipe.json"])
    policy = ScoringPolicy.model_validate_json(raw["policy.json"])
    inputs = PreparedStockoutInputs.model_validate_json(raw["inputs.json"])
    smoke = ServingSmoke.model_validate_json(raw["smoke.json"])
    if (
        recipe != q.recipe
        or policy != q.policy
        or inputs.scope != q.smoke_scope
        or inputs.as_of != q.smoke_as_of
        or len(inputs.points) != q.smoke_rows
        or len(smoke.items) != q.smoke_rows
        or smoke.generated_at > q.created_at
        or read_json(root, "signature.json") != signature(recipe, policy)
    ):
        raise ValueError("stockout_capsule_smoke_signature_or_input_replay")
    verify_smoke(smoke, predict_smoke(inputs, recipe, policy, generated_at=smoke.generated_at))
    for name, reference in (
        ("model_card.json", q.model_card),
        ("inputs.json", q.public_inputs),
        ("smoke.json", q.smoke),
        ("signature.json", q.signature),
    ):
        if receipt(raw[name]) != reference:
            raise ValueError("stockout_capsule_artifact_changed")
    for gate in sorted(REVIEW_GATES):
        name = f"reports/{gate}.json"
        report = read_json(root, name)
        if (
            receipt(raw[name]) != approval.approval.gates[gate].report
            or report.get("qualification_id") != q.qualification_id
            or report.get("gate") != gate
            or report.get("status") != "passed"
        ):
            raise ValueError("stockout_capsule_review_gate_binding")
    card = read_json(root, "model_card.json")
    if (
        card.get("recipe_content_sha256") != canonical_sha256(recipe.model_dump(mode="json"))
        or card.get("policy_content_sha256") != canonical_sha256(policy.model_dump(mode="json"))
        or card.get("quality_status") != q.quality_status
        or card.get("final_campaign_id") != q.final_campaign_id
        or card.get("physical_key") != ["product_id", "stock_location_id", "as_of"]
        or card.get("model_refits") != 0
    ):
        raise ValueError("stockout_capsule_model_card_binding")
    if final:
        from retailops_ai.stockout_lifecycle.card import final_card
        from retailops_ai.stockout_lifecycle.evidence import verify_world_gates

        freeze = CampaignFreeze.model_validate_json(raw["campaign_freeze.json"])
        permission = CampaignPermission.model_validate_json(raw["campaign_permission.json"])
        quality = read_json(root, "final_quality.json")
        bound_recipes(freeze, recipe, policy)
        for world in quality["content"]["worlds"]:
            verify_world_gates(world, freeze)
        expected = aggregate(quality["content"]["worlds"], freeze=freeze, permission=permission)
        if (
            quality != expected
            or quality["content"]["status"] != "passed"
            or freeze.campaign_id != q.final_campaign_id
            or receipt(raw["final_quality.json"]) != q.final_quality
            or card.get("final_quality_id") != quality["quality_id"]
        ):
            raise ValueError("stockout_capsule_independent_final_quality_required")
        if card != final_card(
            selection=read_json(root, "selection.json"),
            freeze=freeze,
            permission=permission,
            recipe=recipe,
            policy=policy,
            quality=quality,
            execution=read_json(root, "execution_evidence.json"),
            inputs=inputs,
        ):
            raise ValueError("stockout_capsule_final_card_or_execution_changed")
    # No mutating alias or DB head operation is performed by a byte verifier.
    if raw != _private(root, capsule_names(final=final)):
        raise ValueError("stockout_capsule_changed_during_verification")
    return approval
