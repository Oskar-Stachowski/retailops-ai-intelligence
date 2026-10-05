"""Actual byte capsules and inference replay; these fixtures never claim final model quality."""

from datetime import UTC, datetime, timedelta

import pytest
from test_stockout_batch import job as job
from test_stockout_conditional_runtime import conditional as conditional
from test_stockout_lifecycle import backend as backend
from test_stockout_lifecycle import sealed
from test_stockout_lifecycle import source as source
from test_stockout_runtime import context as context
from test_stockout_runtime import records as records

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.source_snapshot.files import read_json
from retailops_ai.stockout_lifecycle.contract import (
    REVIEW_GATES,
    StockoutApproval,
    StockoutQualification,
    capsule_names,
)
from retailops_ai.stockout_lifecycle.release import (
    predict_smoke,
    receipt,
    signature,
    verify_approved_capsule,
)


@pytest.fixture
def capsule(job, tmp_path):
    _, inputs, release, _ = job
    recipe = release.binding.approval.qualification.recipe
    policy = release.binding.approval.qualification.policy
    now = datetime.now(UTC)
    smoke = predict_smoke(inputs, recipe, policy, generated_at=now - timedelta(seconds=1))
    card = dict(
        version="stockout-serving-model-card-1.0.0",
        recipe_content_sha256=canonical_sha256(recipe.model_dump(mode="json")),
        policy_content_sha256=canonical_sha256(policy.model_dump(mode="json")),
        quality_status="not_evaluated_mechanics_only",
        final_campaign_id=None,
        physical_key=["product_id", "stock_location_id", "as_of"],
        model_refits=0,
        limitations=["Synthetic mechanics fixture; no quality or deployment acceptance."],
    )
    docs = {
        "recipe.json": recipe.model_dump(mode="json"),
        "policy.json": policy.model_dump(mode="json"),
        "inputs.json": inputs.model_dump(mode="json"),
        "smoke.json": smoke.model_dump(mode="json"),
        "signature.json": signature(recipe, policy),
        "model_card.json": card,
    }
    raw = {name: canonical_bytes(value) for name, value in docs.items()}
    refs = {name: receipt(value).model_dump(mode="json") for name, value in raw.items()}
    q = sealed(
        StockoutQualification,
        "qualification_id",
        "stockout-qualification-serving-sha256-",
        dict(
            version="stockout-serving-qualification-1.0.0",
            purpose="stockout_mechanics_only",
            recipe=recipe.model_dump(mode="json"),
            policy=policy.model_dump(mode="json"),
            model_card=refs["model_card.json"],
            final_quality=None,
            final_campaign_id=None,
            quality_status="not_evaluated_mechanics_only",
            public_inputs=refs["inputs.json"],
            smoke=refs["smoke.json"],
            signature=refs["signature.json"],
            smoke_scope=inputs.scope.model_dump(mode="json"),
            smoke_as_of=inputs.model_dump(mode="json")["as_of"],
            smoke_rows=len(inputs.points),
            created_at=now.isoformat().replace("+00:00", "Z"),
            valid_until=(now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            source_packages_verified=True,
            complete_pipeline_verified=True,
            repeatability_verified=True,
            serving_eligible=False,
        ),
    )
    for gate in REVIEW_GATES:
        name = f"reports/{gate}.json"
        raw[name] = canonical_bytes(
            dict(
                qualification_id=q.qualification_id,
                gate=gate,
                status="passed",
                purpose="isolated_mechanics_only",
            )
        )
    approval = sealed(
        StockoutApproval,
        "release_id",
        "stockout-approval-sha256-",
        dict(
            version="stockout-inference-approval-1.0.0",
            qualification=q.model_dump(mode="json"),
            approval=dict(
                qualification_id=q.qualification_id,
                image_digest="sha256:" + "b" * 64,
                gates={
                    gate: dict(
                        status="passed",
                        report=receipt(raw[f"reports/{gate}.json"]).model_dump(mode="json"),
                    )
                    for gate in REVIEW_GATES
                },
                reason="Synthetic byte capsule acceptance without production approval.",
            ),
            reviewed_by="fixture-reviewer",
            reviewed_at=q.model_dump(mode="json")["created_at"],
            serving_eligible=True,
            registered_in_mlflow=False,
            activated_as_champion=False,
        ),
    )
    raw["qualification.json"] = canonical_bytes(q.model_dump(mode="json"))
    raw["approval.json"] = canonical_bytes(approval.model_dump(mode="json"))
    root = tmp_path / "capsule"
    root.mkdir(mode=0o700)
    for name, value in raw.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(value)
        path.chmod(0o600)
    return root, approval


def test_private_complete_capsule_loads_and_replays_every_smoke_item(capsule):
    root, approval = capsule
    assert verify_approved_capsule(root, approval_id=approval.release_id) == approval
    assert len(capsule_names(final=False)) == 20
    assert "campaign_permission.json" in capsule_names(final=True)
    assert read_json(root, "smoke.json")["serving_eligible"] is False
    assert all(
        r["quality_status"] == "mechanics_only" for r in read_json(root, "smoke.json")["items"]
    )


@pytest.mark.parametrize(
    "name", ["smoke.json", "model_card.json", "signature.json", "reports/calibration.json"]
)
def test_changed_bytes_cannot_be_used_as_approved_capsule(capsule, name):
    root, approval = capsule
    doc = read_json(root, name)
    doc["changed_after_review"] = True
    (root / name).write_bytes(canonical_bytes(doc))
    with pytest.raises(ValueError):
        verify_approved_capsule(root, approval_id=approval.release_id)


@pytest.mark.parametrize("mutation", ["public", "symlink", "extra", "missing", "wrong_approval"])
def test_capsule_rejects_unsafe_inventory_or_identity(capsule, mutation):
    root, approval = capsule
    identity = approval.release_id
    if mutation == "public":
        (root / "smoke.json").chmod(0o644)
    elif mutation == "symlink":
        (root / "inputs.json").rename(root.parent / "inputs.json")
        (root / "inputs.json").symlink_to(root.parent / "inputs.json")
    elif mutation == "extra":
        (root / "extra.json").write_bytes(b"{}")
    elif mutation == "missing":
        (root / "reports/resources.json").unlink()
    else:
        identity = "stockout-approval-sha256-" + "f" * 64
    with pytest.raises(ValueError):
        verify_approved_capsule(root, approval_id=identity)
