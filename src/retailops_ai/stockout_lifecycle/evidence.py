"""Collect bounded public final receipts, preserving every failed quality gate."""

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from retailops_ai.data_contracts.common import CommitSha, Contract, Sha256, UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.model_lifecycle.contracts import Receipt
from retailops_ai.source_snapshot.files import decode_json, read_bytes
from retailops_ai.stockout_campaign.contract import (
    CampaignFreeze,
    CampaignPermission,
    SourceRef,
    require_permission,
)
from retailops_ai.stockout_campaign.gates import quality_gates
from retailops_ai.stockout_campaign.report import aggregate
from retailops_ai.stockout_campaign.scenarios import REQUIRED
from retailops_ai.stockout_lifecycle.release import receipt
from retailops_ai.stockout_qualification.fit import calibration_error

LIMITS = dict(
    tree_rss_bytes=1280 * 1024**2,
    scratch_bytes=640 * 1024**2,
    wall_seconds=2700,
    minimum_free_bytes=6 * 1024**3,
)
PUBLIC_NAMES = {"native.json", "wheel.json", "native-access.jsonl", "wheel-access.jsonl"}


class Phase(Contract):
    phase: Literal["native", "wheel"]
    exit_code: Literal[0]
    wall_seconds: Annotated[float, Field(gt=0, le=2700)]


class Measurement(Contract):
    wall_seconds: Annotated[float, Field(gt=0, le=2700)]
    sampled_tree_peak_rss_bytes: Annotated[int, Field(gt=0, le=LIMITS["tree_rss_bytes"])]
    sampled_peak_scratch_logical_bytes: Annotated[int, Field(gt=0, le=LIMITS["scratch_bytes"])]
    sampled_peak_scratch_allocated_bytes: Annotated[int, Field(gt=0, le=LIMITS["scratch_bytes"])]
    minimum_free_bytes: Annotated[int, Field(ge=LIMITS["minimum_free_bytes"])]
    samples: Annotated[int, Field(ge=1)]


class Resource(Contract):
    schema_version: Literal["stockout-final-world-resource-1.0.0"]
    status: Literal["passed"]
    campaign_id: str
    world: Literal["matching", "future_stress"]
    seed: Literal[42, 137, 2026]
    budgets: dict[str, int]
    execution_code_sha256: Sha256
    dependency_lock_sha256: Sha256
    execution_commit: CommitSha
    workflow_run_id: Annotated[int, Field(ge=1)]
    workflow_run_attempt: Annotated[int, Field(ge=1)]
    phases: tuple[Phase, Phase]
    native_wheel_equal: Literal[True]
    receipts: dict[str, Receipt]
    recorded_at: UtcTime
    failure: None
    measurement: Measurement
    model_refits: Literal[0]
    model_promoted: Literal[False]
    ai08_ready: Literal[False]


class Access(Contract):
    at: UtcTime
    campaign_id: str
    source_dataset_id: str
    world: Literal["matching", "future_stress"]
    seed: Literal[42, 137, 2026]
    permission_sha256: Sha256
    approved_by: str
    status: str
    model_refits: Literal[0]


def verify_world_gates(report: dict[str, Any], freeze: CampaignFreeze) -> None:
    """A changed 'passed' flag cannot conceal failed support, ranking or calibration."""
    gates = quality_gates(
        report["metrics"],
        expected_categories=set(freeze.expected_categories),
        expected_locations=set(freeze.expected_stock_locations),
        policy=freeze.quality_requirements,
    )
    expected = {k: gates[k] for k in ("status", "segments", "required_segment_universe_complete")}
    expected["data_role"] = "authorized_final_test"
    if report["segment_gates"] != expected:
        raise ValueError("stockout_final_segment_gate_measurement_changed")
    scenarios = {}
    if report["world"] == "future_stress":
        for name in REQUIRED:
            result = report["metrics"]["scenario:" + name]
            enough = result["rows"] >= 20 and min(result["positives"], result["negatives"]) >= 5
            checks = dict(support=enough)
            if enough:
                checks.update(
                    AP_above_no_skill=result["average_precision"] > result["prevalence"],
                    brier_better_than_train_constant=result["brier"]
                    < result["train_prevalence_constant_brier"],
                    calibration_error_in_budget=calibration_error(result) <= 0.15,
                )
            scenarios[name] = dict(
                status="not_evaluable"
                if not enough
                else "passed"
                if all(checks.values())
                else "failed",
                checks=checks,
            )
        for name in ("promotion", "demand_shock", "inventory_constraint"):
            result = report["metrics"]["scenario:control:" + name]
            scenarios["control:" + name] = dict(
                status="passed" if result["rows"] >= 20 else "not_evaluable",
                rows=result["rows"],
                interpretation="coverage_control_no_causal_effect_claim",
            )
    if report["scenario_gates"] != scenarios:
        raise ValueError("stockout_final_scenario_gate_measurement_changed")


def _world(
    root: Path, source: SourceRef, freeze: CampaignFreeze, permission: CampaignPermission
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = {name: read_bytes(root, name) for name in PUBLIC_NAMES}
    raw["resource.json"] = read_bytes(root, "resource.json", limit=64 * 1024)
    return _world_documents(raw, source, freeze, permission)


def _world_documents(
    raw: dict[str, bytes], source: SourceRef, freeze: CampaignFreeze, permission: CampaignPermission
) -> tuple[dict[str, Any], dict[str, Any]]:
    resource_raw = raw["resource.json"]
    resource = Resource.model_validate_json(resource_raw)
    native = decode_json(raw["native.json"])
    if (
        resource.campaign_id != freeze.campaign_id
        or (resource.world, resource.seed) != (source.world, source.seed)
        or resource.budgets != LIMITS
        or resource.execution_code_sha256 != freeze.evaluator_code_sha256
        or resource.dependency_lock_sha256 != freeze.dependency_lock_sha256
        or [p.phase for p in resource.phases] != ["native", "wheel"]
        or set(resource.receipts) != PUBLIC_NAMES
        or any(resource.receipts[name] != receipt(raw[name]) for name in PUBLIC_NAMES)
        or native != decode_json(raw["wheel.json"])
        or any(
            raw[name] != canonical_bytes(decode_json(raw[name])) + b"\n"
            for name in ("native.json", "wheel.json", "resource.json")
        )
    ):
        raise ValueError("stockout_final_resource_or_native_wheel_receipt_changed")
    verify_world_gates(native, freeze)
    audits = {}
    for phase in ("native", "wheel"):
        lines = raw[phase + "-access.jsonl"].splitlines()
        if len(lines) != 2:
            raise ValueError("stockout_final_access_audit_incomplete")
        events = [Access.model_validate_json(line) for line in lines]
        audits[phase] = [decode_json(line) for line in lines]
        if raw[phase + "-access.jsonl"] != b"".join(
            canonical_bytes(e) + b"\n" for e in audits[phase]
        ):
            raise ValueError("stockout_final_access_audit_canonical_bytes_required")
        if (
            [e.status for e in events]
            != ["authorized_before_private_read", "complete_" + native["status"]]
            or not permission.approved_at <= events[0].at <= events[1].at <= resource.recorded_at
            or any(
                e.campaign_id != freeze.campaign_id
                or e.source_dataset_id != source.source_dataset_id
                or (e.world, e.seed) != (source.world, source.seed)
                or e.permission_sha256 != canonical_sha256(permission.model_dump(mode="json"))
                or e.approved_by != permission.approved_by
                for e in events
            )
        ):
            raise ValueError("stockout_final_access_audit_authorization_changed")
    return native, dict(
        world=source.world,
        seed=source.seed,
        resource=decode_json(resource_raw),
        resource_receipt=receipt(resource_raw).model_dump(mode="json"),
        access_audits=audits,
    )


def collect(
    roots: dict[tuple[str, int], Path],
    *,
    freeze: CampaignFreeze,
    permission: CampaignPermission | None,
) -> dict[str, Any]:
    """Only small reports/audits are read; source archives and final outcomes stay closed."""
    require_permission(freeze, permission)
    if permission is None:
        raise ValueError("stockout_final_owner_permission_required")
    if set(roots) != {(s.world, s.seed) for s in freeze.sources}:
        raise ValueError("stockout_final_six_receipt_directories_required")
    pairs = [_world(roots[s.world, s.seed], s, freeze, permission) for s in freeze.sources]
    quality = aggregate([r for r, _ in pairs], freeze=freeze, permission=permission)
    resources = [r for _, r in pairs]
    identities = {
        (
            r["resource"]["execution_commit"],
            r["resource"]["workflow_run_id"],
            r["resource"]["workflow_run_attempt"],
        )
        for r in resources
    }
    if len(identities) != 1:
        raise ValueError("stockout_final_execution_campaign_receipts_mixed")
    content = dict(
        version="stockout-final-execution-evidence-1.0.0",
        campaign_id=freeze.campaign_id,
        final_quality_id=quality["quality_id"],
        worlds=resources,
        native_wheel_equal=True,
        all_resource_budgets_passed=True,
        authorized_access_audits_verified=True,
        source_packages_full_replay=True,
        model_refits=0,
        model_promoted=False,
        ai08_ready=False,
    )
    return dict(
        final_quality=quality,
        execution_evidence=dict(
            evidence_id="stockout-final-execution-sha256-" + canonical_sha256(content),
            content=content,
        ),
    )


def verify_execution_evidence(
    value: dict[str, Any],
    quality: dict[str, Any],
    *,
    freeze: CampaignFreeze,
    permission: CampaignPermission,
) -> None:
    """Replay the public byte receipts even after the six source archives are archived."""
    require_permission(freeze, permission)
    expected_quality = aggregate(quality["content"]["worlds"], freeze=freeze, permission=permission)
    if quality != expected_quality:
        raise ValueError("stockout_final_execution_quality_changed")
    content = value["content"]
    worlds = content["worlds"]
    if len(worlds) != 6 or {(w["world"], w["seed"]) for w in worlds} != {
        (s.world, s.seed) for s in freeze.sources
    }:
        raise ValueError("stockout_final_execution_world_inventory_changed")
    reproduced = []
    for source in freeze.sources:
        world = next(w for w in worlds if (w["world"], w["seed"]) == (source.world, source.seed))
        report = next(
            r
            for r in quality["content"]["worlds"]
            if (r["world"], r["seed"]) == (source.world, source.seed)
        )
        raw = {phase + ".json": canonical_bytes(report) + b"\n" for phase in ("native", "wheel")}
        raw["resource.json"] = canonical_bytes(world["resource"]) + b"\n"
        for phase in ("native", "wheel"):
            raw[phase + "-access.jsonl"] = b"".join(
                canonical_bytes(e) + b"\n" for e in world["access_audits"][phase]
            )
        _, verified = _world_documents(raw, source, freeze, permission)
        if world != verified:
            raise ValueError("stockout_final_execution_resource_changed")
        reproduced.append(verified)
    identities = {
        (
            w["resource"]["execution_commit"],
            w["resource"]["workflow_run_id"],
            w["resource"]["workflow_run_attempt"],
        )
        for w in reproduced
    }
    expected = dict(
        version="stockout-final-execution-evidence-1.0.0",
        campaign_id=freeze.campaign_id,
        final_quality_id=quality["quality_id"],
        worlds=reproduced,
        native_wheel_equal=True,
        all_resource_budgets_passed=True,
        authorized_access_audits_verified=True,
        source_packages_full_replay=True,
        model_refits=0,
        model_promoted=False,
        ai08_ready=False,
    )
    if (
        len(identities) != 1
        or content != expected
        or value.get("evidence_id")
        != "stockout-final-execution-sha256-" + canonical_sha256(expected)
    ):
        raise ValueError("stockout_final_execution_evidence_identity_changed")
