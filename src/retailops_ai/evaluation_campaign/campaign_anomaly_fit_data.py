"""Actual complete native preparation, fit and saved validation replay inputs.

Internal numerical/physical operation; its campaign controller reserves and
measures the enclosing attempt. No truth, final source or quality selection.
"""

import hashlib
from pathlib import Path
from time import perf_counter
from typing import Any

from retailops_ai.anomaly_detectors.contract import Family
from retailops_ai.anomaly_detectors.protocol import Scope
from retailops_ai.anomaly_evaluation.contract import Decision
from retailops_ai.anomaly_evaluation.verification import verify_scores
from retailops_ai.anomaly_portfolio.model import CensusCountRateDescriptor, Model, load
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_anomaly_day_gate import (
    CampaignAnomalyDayGatePlan,
    CampaignAnomalyDiskDayGate,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_days import (
    CampaignAnomalyDayDiscoveryPlan,
    CampaignAnomalyDayProjection,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_features import (
    CampaignAnomalyFeaturePlan,
    CampaignAnomalyFeatureProjection,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_fit_contract import CampaignAnomalyFitPlan
from retailops_ai.evaluation_campaign.campaign_anomaly_membership import (
    CampaignAnomalyMembershipPlan,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_parent import (
    CampaignAnomalyParentPlan,
    CampaignAnomalyPublicParent,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_replay import (
    CAPTURE_VERSION,
    CampaignAnomalyDiskReplay,
    CampaignAnomalyReplayPlan,
    ProjectDelivery,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_scoring import iter_anomaly_census_scores
from retailops_ai.evaluation_campaign.campaign_anomaly_training import fit_anomaly_membership_census
from retailops_ai.evaluation_campaign.campaign_export import _producer_values
from retailops_ai.evaluation_campaign.campaign_generation_worker import write
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
from retailops_ai.raw_dq.contract import stamp
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    decode_json,
    file_hash,
    read_bytes,
    regular_file,
)

MAX_MANIFEST_BYTES = 32 * 1024**2
MAX_REPLAY_ROW_BYTES = 65536
FAMILIES: tuple[Family, ...] = ("seasonal_residual", "isolation_forest")


def _capture(
    parent: CampaignAnomalyPublicParent, root: Path, plan: CampaignAnomalyFitPlan
) -> CampaignAnomalyReplayPlan:
    """Reconstruct every canonical event at its actual public ingestion time.

    These are explicitly reconstructed Source deliveries, not a claim that a
    live broker capture or a publisher progress record has been observed.
    """
    trace = hashlib.sha256()
    size = count = 0
    previous = None
    with (root / "capture.jsonl").open("xb") as output:
        query = (
            "SELECT event,event_sha256,fact,fact_sha256 FROM parents "
            "ORDER BY json_extract(CAST(fact AS TEXT),'$.ingested_at'),occurred,event_type,business_id"
        )
        for event_raw, event_hash, fact_raw, fact_hash in parent._db().execute(query):
            event = parent._checked(event_raw, event_hash)
            fact = parent._checked(fact_raw, fact_hash)
            # Recheck the actual operational match, not just a private JSON key.
            if parent.parent.match(event)[1] != fact:
                raise SnapshotError("campaign_anomaly_fit_capture_parent_mismatch")
            received = stamp(fact["ingested_at"])
            if previous is not None and received < previous:
                raise SnapshotError("campaign_anomaly_fit_capture_time_order")
            previous = received
            body = {
                "contract_version": CAPTURE_VERSION,
                "kind": "event",
                "topic": "retailops.sales.v1",
                "partition": 0,
                "offset": count,
                "received_at": received.isoformat(),
                "body_utf8": canonical_json(event).decode(),
            }
            record = ProjectDelivery.model_validate_json(
                canonical_bytes(
                    {**body, "record_id": "raw-record-sha256-" + canonical_sha256(body)}
                )
            )
            raw = canonical_bytes(record.model_dump(mode="json")) + b"\n"
            count += 1
            size += len(raw)
            if len(raw) > MAX_REPLAY_ROW_BYTES or size > plan.max_capture_bytes:
                raise SnapshotError("campaign_anomaly_fit_capture_budget")
            output.write(raw)
            trace.update(raw)
    if count != parent.plan.parent_events:
        raise SnapshotError("campaign_anomaly_fit_capture_incomplete")
    return CampaignAnomalyReplayPlan(
        capture_sha256=trace.hexdigest(),
        capture_records=count,
        parent_events=count,
        max_index_bytes=plan.max_index_bytes,
        max_group_rows=plan.max_series_rows,
        max_group_bytes=plan.max_series_bytes,
    )


def _membership(
    features: CampaignAnomalyFeatureProjection, plan: CampaignAnomalyFitPlan
) -> CampaignAnomalyMembershipPlan:
    scopes: list[Scope] = []
    query = "SELECT DISTINCT event_type,product_id,location_id,channel,currency FROM points ORDER BY event_type,product_id,location_id,channel,currency"
    for row in features._db().execute(query):
        if len(scopes) >= plan.max_series:
            raise SnapshotError("campaign_anomaly_fit_scope_budget")
        scopes.append(Scope.model_validate(dict(zip(Scope.model_fields, row, strict=True))))
    return CampaignAnomalyMembershipPlan(
        feature_plan_sha256=canonical_sha256(features.plan.model_dump(mode="json")),
        native_points_sha256=features.native_points_sha256,
        scopes=tuple(scopes),
        train=plan.train,
        validation=plan.validation,
        test=plan.test,
        training_cutoff=plan.training_cutoff,
        selection_cutoff=plan.selection_cutoff,
        max_requested_rows=plan.max_days,
        max_series_bytes=plan.max_series_bytes,
    )


def fit_physical_anomaly_parent(
    snapshot: Path,
    curated: Path,
    root: Path,
    *,
    plan: CampaignAnomalyFitPlan,
    source: PhysicalSourceSpec,
    runtime: PreparationRuntime,
    producer_commit: str,
    producer_lock: str,
    exporter_lock: str | None,
) -> dict[str, Any]:
    """Return only after all public/feature/replay contexts actually complete."""
    plan = CampaignAnomalyFitPlan.model_validate_json(plan.model_dump_json())
    raw = read_bytes(curated, "curated_manifest.json", 2 * 1024**2)
    if hashlib.sha256(raw).hexdigest() != source.curated_manifest_sha256:
        raise SnapshotError("campaign_anomaly_fit_curated_manifest_pin")
    counts = {r["table"]: r["row_count"] for r in decode_json(raw)["tables"]}
    parent_plan = CampaignAnomalyParentPlan(
        source=source,
        runtime=runtime,
        parent_events=counts["sales"] + counts["return_events"],
        max_index_bytes=plan.max_index_bytes,
    )
    parent = CampaignAnomalyPublicParent(snapshot, curated, parent_plan, root / "tmp")
    validation_files: dict[str, Any] = {}
    with parent:
        _producer_values(parent._replay, producer_commit, producer_lock, exporter_lock)
        days = CampaignAnomalyDayProjection(
            parent,
            CampaignAnomalyDayDiscoveryPlan(
                parent_plan_sha256=canonical_sha256(parent.plan.model_dump(mode="json")),
                parent_events_sha256=parent.native_events_sha256,
                max_days=plan.max_days,
                max_index_bytes=plan.max_index_bytes,
                max_series=plan.max_series,
                max_series_rows=plan.max_series_rows,
                max_series_bytes=plan.max_series_bytes,
            ),
            root / "tmp",
        )
        with days:
            replay_plan = _capture(parent, root, plan)
            replay = CampaignAnomalyDiskReplay(parent.parent, replay_plan, root / "tmp")
            with replay:
                with regular_file(root, "capture.jsonl") as stream:
                    while row := stream.readline(MAX_REPLAY_ROW_BYTES + 1):
                        if len(row) > MAX_REPLAY_ROW_BYTES:
                            raise SnapshotError("campaign_anomaly_fit_capture_line_budget")
                        replay.consume(decode_json(row))
                replay_result = replay.finish()
                if (
                    replay_result["accepted_parent_facts"] != parent.plan.parent_events
                    or replay_result["missing_parent_facts"] != 0
                ):
                    raise SnapshotError("campaign_anomaly_fit_incomplete_native_replay")
                gate = CampaignAnomalyDiskDayGate(
                    days,
                    replay,
                    CampaignAnomalyDayGatePlan(
                        day_plan_sha256=canonical_sha256(days.plan.model_dump(mode="json")),
                        native_days_sha256=days.native_days_sha256,
                        replay_plan_sha256=canonical_sha256(replay.plan.model_dump(mode="json")),
                        accepted_facts=replay_result["accepted_parent_facts"],
                        quarantined_records=sum(1 for _ in replay.rows("quarantine")),
                    ),
                )
                with gate:
                    features = CampaignAnomalyFeatureProjection(
                        gate,
                        CampaignAnomalyFeaturePlan(
                            day_gate_plan_sha256=canonical_sha256(
                                gate.plan.model_dump(mode="json")
                            ),
                            native_days_sha256=days.native_days_sha256,
                            expected_points=len(days.days),
                            max_index_bytes=plan.max_index_bytes,
                            max_context_rows=plan.max_series_rows,
                            max_context_bytes=plan.max_series_bytes,
                        ),
                        root / "tmp",
                    )
                    with features:
                        membership = _membership(features, plan)
                        fitted = fit_anomaly_membership_census(
                            features.training_memberships(membership),
                            membership,
                            plan.policy,
                            plan.event_capacities,
                            scratch=root / "tmp",
                            max_role_bytes=plan.max_role_bytes,
                        )
                        manifest = {
                            "version": "ai09-complete-anomaly-feature-manifest-1.0.0",
                            "source": source.model_dump(mode="json"),
                            "runtime": runtime.model_dump(mode="json"),
                            "parent_plan": parent.plan.model_dump(mode="json"),
                            "day_plan": days.plan.model_dump(mode="json"),
                            "resolved_days": len(days.days),
                            "replay_plan": replay.plan.model_dump(mode="json"),
                            "gate_plan": gate.plan.model_dump(mode="json"),
                            "feature_plan": features.plan.model_dump(mode="json"),
                            "membership_plan": membership.model_dump(mode="json"),
                            "native_points_sha256": features.native_points_sha256,
                        }
                        if len(canonical_bytes(manifest)) > MAX_MANIFEST_BYTES:
                            raise SnapshotError("campaign_anomaly_fit_feature_manifest_budget")
                        descriptor = CensusCountRateDescriptor(
                            source_dataset_id=source.parent.source_dataset_id,
                            feature_manifest_sha256=canonical_sha256(manifest),
                            feature_rows_sha256=features.native_points_sha256,
                            train=plan.train,
                            validation=plan.validation,
                            training_cutoff=plan.training_cutoff,
                            selection_cutoff=plan.selection_cutoff,
                            policy=plan.policy,
                            event_capacities=plan.event_capacities,
                            groups=fitted.groups,
                            training_membership_sha256=fitted.training_membership_sha256,
                            validation_membership_sha256=fitted.validation_membership_sha256,
                            code_sha256=runtime.code_sha256,
                            dependency_lock_sha256=runtime.dependency_lock_sha256,
                        )
                        model = Model(
                            detector_id="anomaly-detector-sha256-"
                            + canonical_sha256(descriptor.model_dump(mode="json")),
                            descriptor=descriptor,
                        )
                        for family in FAMILIES:
                            name = "validation-" + family + ".jsonl"
                            digest, count, size = hashlib.sha256(), 0, 0
                            with (root / name).open("xb") as output:
                                for scored in iter_anomaly_census_scores(
                                    model,
                                    features.scoring_points(membership.scopes, plan.validation),
                                    membership.scopes,
                                    plan.validation,
                                    family,
                                    "validation",
                                    plan.selection_cutoff,
                                    max_requested_rows=plan.max_days,
                                ):
                                    payload = {
                                        "decision": scored.decision.model_dump(mode="json"),
                                        "row": scored.model_row.model_dump(mode="json")
                                        if scored.model_row is not None
                                        else None,
                                    }
                                    raw = canonical_bytes(payload) + b"\n"
                                    count += 1
                                    size += len(raw)
                                    if (
                                        len(raw) > MAX_REPLAY_ROW_BYTES
                                        or size > plan.resources.scratch_bytes
                                    ):
                                        raise SnapshotError(
                                            "campaign_anomaly_fit_validation_budget"
                                        )
                                    output.write(raw)
                                    digest.update(raw)
                            expected = len(membership.scopes) * (
                                (plan.validation.end - plan.validation.start).days + 1
                            )
                            if count != expected:
                                raise SnapshotError("campaign_anomaly_fit_validation_incomplete")
                            validation_files[name] = {
                                "rows": count,
                                "bytes": size,
                                "sha256": digest.hexdigest(),
                            }
    # A late parent/feature/replay failure cannot publish a model bundle.
    if runtime_pin() != runtime:
        raise SnapshotError("campaign_anomaly_fit_runtime_changed")
    completion = {
        "parent": parent.receipt(),
        "days": days.receipt(),
        "gate": gate.receipt(),
        "features": features.receipt(),
        "replay": replay_result,
    }
    bundle = root / "bundle"
    bundle.mkdir(mode=0o700)
    write(bundle / "model.json", model.model_dump(mode="json"))
    write(bundle / "feature-manifest.json", manifest)
    write(bundle / "parent-completion.json", completion)
    write(bundle / "plan.json", plan.model_dump(mode="json"))
    model_size, model_hash = file_hash(bundle, "model.json")
    if model_size > 8 * 1024**2:
        raise SnapshotError("campaign_anomaly_fit_native_model_budget")
    return {
        "model_id": model.detector_id,
        "model_sha256": model_hash,
        "feature_manifest_sha256": descriptor.feature_manifest_sha256,
        "all_parent_contexts_completed": True,
        "training_membership_sha256": fitted.training_membership_sha256,
        "validation_membership_sha256": fitted.validation_membership_sha256,
        "membership_counts": fitted.membership_counts,
        "requested_rows": fitted.requested_rows,
        "fit_resources": [r.model_dump(mode="json") for r in fitted.fit_resources],
        "role_file_bytes": fitted.role_file_bytes,
        "validation_files": validation_files,
    }


def reload_physical_anomaly(root: Path, fitted: dict[str, Any]) -> dict[str, Any]:
    """Cold load actual data-only model and replay every saved validation row."""
    started = perf_counter()
    model = load(root / "bundle/model.json", fitted["model_sha256"])
    loaded = perf_counter()
    if model.detector_id != fitted["model_id"]:
        raise SnapshotError("campaign_anomaly_fit_reload_model_binding")
    if set(fitted["validation_files"]) != {"validation-" + f + ".jsonl" for f in FAMILIES}:
        raise SnapshotError("campaign_anomaly_fit_reload_family_inventory")
    observed = {}
    for name, expected in fitted["validation_files"].items():
        trace, count, size = hashlib.sha256(), 0, 0
        decisions: list[Decision] = []
        inputs: list[Any] = []
        with regular_file(root, name) as stream:
            while raw := stream.readline(MAX_REPLAY_ROW_BYTES + 1):
                size += len(raw)
                count += 1
                if (
                    len(raw) > MAX_REPLAY_ROW_BYTES
                    or size > expected["bytes"]
                    or count > expected["rows"]
                ):
                    raise SnapshotError("campaign_anomaly_fit_reload_extent")
                row = decode_json(raw)
                if set(row) != {"decision", "row"} or canonical_bytes(row) + b"\n" != raw:
                    raise SnapshotError("campaign_anomaly_fit_reload_row_encoding")
                decision = Decision.model_validate_json(canonical_bytes(row["decision"]))
                if name != "validation-" + decision.family + ".jsonl":
                    raise SnapshotError("campaign_anomaly_fit_reload_family")
                decisions.append(decision)
                inputs.append(row["row"])
                trace.update(raw)
                if len(decisions) == 8192:
                    verify_scores(model, decisions, inputs)
                    decisions.clear()
                    inputs.clear()
        if decisions:
            verify_scores(model, decisions, inputs)
        if (count, size, trace.hexdigest()) != (
            expected["rows"],
            expected["bytes"],
            expected["sha256"],
        ):
            raise SnapshotError("campaign_anomaly_fit_reload_identity")
        observed[name] = count
    return {
        "model_id": model.detector_id,
        "all_validation_rows_replayed": True,
        "rows": observed,
        "cold_model_load_wall_seconds": loaded - started,
        "validation_replay_wall_seconds": perf_counter() - loaded,
    }
