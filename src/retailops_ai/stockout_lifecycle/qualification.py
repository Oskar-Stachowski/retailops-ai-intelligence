"""Create a private final qualification and record a separate authenticated review."""

import os
import shutil
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.source_snapshot.files import checked_directory, read_json
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace
from retailops_ai.stockout_campaign.contract import (
    CampaignFreeze,
    CampaignPermission,
    require_permission,
)
from retailops_ai.stockout_lifecycle.card import final_card
from retailops_ai.stockout_lifecycle.contract import (
    REVIEW_GATES,
    ApprovalRequest,
    StockoutApproval,
    StockoutQualification,
    capsule_names,
)
from retailops_ai.stockout_lifecycle.evidence import LIMITS, collect
from retailops_ai.stockout_lifecycle.release import (
    MAX_CAPSULE_BYTES,
    _private,
    predict_smoke,
    receipt,
    signature,
    verify_approved_capsule,
)
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_runtime.inputs import PhysicalScope, prepare_inputs


def _publish(
    output: Path,
    identity: str,
    documents: dict[str, bytes],
    *,
    approval_id: str | None = None,
    forbidden: tuple[Path, ...] = (),
) -> Path:
    if any(
        output.resolve().is_relative_to(p.resolve()) or p.resolve().is_relative_to(output.resolve())
        for p in forbidden
    ):
        raise ValueError("stockout_release_output_must_not_overlap_inputs")
    output.mkdir(mode=0o700, parents=False, exist_ok=True)
    checked_directory(output)
    if output.stat().st_uid != os.geteuid() or output.stat().st_mode & 0o077:
        raise ValueError("stockout_release_private_output_required")
    if sum(map(len, documents.values())) > MAX_CAPSULE_BYTES:
        raise ValueError("stockout_release_total_byte_limit")
    with tempfile.TemporaryDirectory(prefix=".stockout-release-", dir=output) as temporary:
        root = Path(temporary) / "complete"
        root.mkdir(mode=0o700)
        for name, raw in sorted(documents.items()):
            target = root / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        if approval_id is not None:
            verify_approved_capsule(root, approval_id=approval_id)
        fsync_tree(root)
        destination = output / identity
        publish_noreplace(root, destination)
        return destination


def qualify_final(
    *,
    freeze: CampaignFreeze,
    permission: CampaignPermission | None,
    recipe: ScoringRecipe,
    policy: ScoringPolicy,
    receipt_roots: dict[tuple[str, int], Path],
    selection_path: Path,
    curated: Path,
    features: Path,
    upstream: Path,
    scope: PhysicalScope,
    as_of: datetime,
    output: Path,
) -> Path:
    """Replay public inference parents only after all six independent quality reports pass."""
    require_permission(freeze, permission)
    result = collect(receipt_roots, freeze=freeze, permission=permission)
    if permission is None or result["final_quality"]["content"]["status"] != "passed":
        raise ValueError("stockout_final_quality_not_ready_no_qualification")
    if (
        sys.platform != "linux"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("GITHUB_REPOSITORY") != "Oskar-Stachowski/retailops-ai-intelligence"
        or shutil.disk_usage(curated.parent).free < LIMITS["minimum_free_bytes"]
    ):
        raise ValueError("stockout_final_qualification_requires_owned_remote_runner_reserve")
    selection = read_json(selection_path.parent, selection_path.name)
    inputs = prepare_inputs(curated, features, upstream, scope=scope, as_of=as_of)
    if not any(p.feature.status == "eligible" for p in inputs.points):
        raise ValueError("stockout_final_qualification_requires_eligible_scoring_smoke")
    card = final_card(
        selection=selection,
        freeze=freeze,
        permission=permission,
        recipe=recipe,
        policy=policy,
        quality=result["final_quality"],
        execution=result["execution_evidence"],
        inputs=inputs,
    )
    now = datetime.now(UTC)
    if any(
        datetime.fromisoformat(w["resource"]["recorded_at"]) > now
        for w in result["execution_evidence"]["content"]["worlds"]
    ):
        raise ValueError("stockout_final_qualification_execution_receipt_in_future")
    smoke = predict_smoke(inputs, recipe, policy, generated_at=now)
    documents: dict[str, Any] = {
        "recipe.json": recipe.model_dump(mode="json"),
        "policy.json": policy.model_dump(mode="json"),
        "inputs.json": inputs.model_dump(mode="json"),
        "smoke.json": smoke.model_dump(mode="json"),
        "signature.json": signature(recipe, policy),
        "model_card.json": card,
        "selection.json": selection,
        "execution_evidence.json": result["execution_evidence"],
        "final_quality.json": result["final_quality"],
        "campaign_freeze.json": freeze.model_dump(mode="json"),
        "campaign_permission.json": permission.model_dump(mode="json"),
    }
    raw = {name: canonical_bytes(value) + b"\n" for name, value in documents.items()}
    content = dict(
        version="stockout-serving-qualification-1.0.0",
        purpose="qualified_stockout",
        recipe=recipe.model_dump(mode="json"),
        policy=policy.model_dump(mode="json"),
        model_card=receipt(raw["model_card.json"]).model_dump(mode="json"),
        final_quality=receipt(raw["final_quality.json"]).model_dump(mode="json"),
        final_campaign_id=freeze.campaign_id,
        quality_status="passed_independent_final_campaign",
        public_inputs=receipt(raw["inputs.json"]).model_dump(mode="json"),
        smoke=receipt(raw["smoke.json"]).model_dump(mode="json"),
        signature=receipt(raw["signature.json"]).model_dump(mode="json"),
        smoke_scope=inputs.scope.model_dump(mode="json"),
        smoke_as_of=inputs.model_dump(mode="json")["as_of"],
        smoke_rows=len(inputs.points),
        created_at=now.isoformat().replace("+00:00", "Z"),
        valid_until=(now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        source_packages_verified=True,
        complete_pipeline_verified=True,
        repeatability_verified=True,
        serving_eligible=False,
    )
    content["qualification_id"] = "stockout-qualification-serving-sha256-" + canonical_sha256(
        content
    )
    q = StockoutQualification.model_validate_json(canonical_bytes(content))
    raw["qualification.json"] = canonical_bytes(q.model_dump(mode="json")) + b"\n"
    return _publish(
        output,
        q.qualification_id,
        raw,
        forbidden=(curated, features, upstream, selection_path.parent, *receipt_roots.values()),
    )


def approve_stockout(
    qualification: Path, *, actor: Principal, request: ApprovalRequest, reports: Path, output: Path
) -> Path:
    """Record the operator's explicit review; registration and promotion remain separate."""
    if "promoter" not in actor.roles or "model:decide" not in actor.capabilities:
        raise ValueError("stockout_approval_promoter_required")
    request = ApprovalRequest.model_validate_json(request.model_dump_json())
    names = (
        capsule_names(final=True) - {"approval.json"} - {f"reports/{g}.json" for g in REVIEW_GATES}
    )
    raw = _private(qualification, names)
    q = StockoutQualification.model_validate_json(raw["qualification.json"])
    if q.purpose != "qualified_stockout" or request.qualification_id != q.qualification_id:
        raise ValueError("stockout_approval_qualified_final_identity_required")
    raw.update(_private(reports, {f"reports/{g}.json" for g in REVIEW_GATES}))
    content = dict(
        version="stockout-inference-approval-1.0.0",
        qualification=q.model_dump(mode="json"),
        approval=request.model_dump(mode="json"),
        reviewed_by=actor.principal_id,
        reviewed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        serving_eligible=True,
        registered_in_mlflow=False,
        activated_as_champion=False,
    )
    content["release_id"] = "stockout-approval-sha256-" + canonical_sha256(content)
    approval = StockoutApproval.model_validate_json(canonical_bytes(content))
    raw["approval.json"] = canonical_bytes(approval.model_dump(mode="json")) + b"\n"
    return _publish(
        output,
        approval.release_id,
        raw,
        approval_id=approval.release_id,
        forbidden=(qualification, reports),
    )
