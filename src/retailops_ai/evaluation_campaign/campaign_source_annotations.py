"""Bind preregistered scenario metadata to the public Source manifest.

The caller must prove the completed generation and reserve the entire source
read before supplying a verified Snapshot. This component grants no access and
does not establish preregistration timing or full-source qualification. It never
opens the private scenario/effects artifact. Labels remain evaluation metadata.
"""

from collections import defaultdict
from datetime import date
from typing import Any

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_generation_contract import CampaignGenerationPlan
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    Anomaly,
    CampaignForecastContextScope,
    CampaignSourceKeyAnnotation,
    Scenario,
)
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.source_snapshot.files import SnapshotError, decode_json, json_sha256
from retailops_ai.source_snapshot.inventory_protocol import resource_bytes, validate_schema
from retailops_ai.source_snapshot.protocol import Snapshot

DEMAND = "business-anomaly-plan-1.0.0"
PHYSICAL = "business-physical-anomaly-plan-1.0.0"


class SourceAnnotations:
    """Immutable, bounded windows from a frozen plan, without observed outcomes."""

    def __init__(
        self,
        snapshot: Snapshot,
        generation: CampaignGenerationPlan,
        scope: CampaignForecastContextScope,
    ) -> None:
        self.scope = CampaignForecastContextScope.model_validate_json(scope.model_dump_json())
        generation = CampaignGenerationPlan.model_validate_json(generation.model_dump_json())
        manifest = decode_json(canonical_bytes(snapshot.manifest))
        source, descriptor = manifest["source"], manifest["source"]["descriptor"]
        if (
            manifest["schema_version"] != generation.snapshot_schema_version
            or manifest["snapshot_id"] != self.scope.snapshot_id
            or manifest["source_dataset_id"] != self.scope.source_dataset_id
            or source["dataset_id"] != self.scope.source_dataset_id
            or descriptor["resolved_parameters"]["seed"] != self.scope.data_seed
            or descriptor["resolved_parameters"] != generation.resolved_parameters
            or source["requested_parameters"] != generation.requested_parameters
            or generation.source_recipe_sha256 != self.scope.source_recipe_sha256
            or manifest["descriptor"]["include_evaluation_truth"] is not False
            or manifest["snapshot_ready"] is not True
            or source["facts_ready"] is not True
        ):
            raise SnapshotError("campaign_annotations_source_generation_scope_mismatch")
        self._windows: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        self.source_manifest_sha256 = json_sha256(source)
        if generation.entrypoint == "cached_inventory_v2":
            if (
                source["schema_version"] != "2.7.0"
                or self.scope.source_scenario_plan_sha256 is not None
                or generation.scenario_plan is not None
                or "scenario_plan_sha256" in descriptor
            ):
                raise SnapshotError("campaign_annotations_unplanned_source_has_scenario")
            self.plan_sha256 = None
            return
        if source["schema_version"] != "2.8.0" or generation.scenario_plan is None:
            raise SnapshotError("campaign_annotations_planned_source_required")
        plan = decode_json(canonical_bytes(generation.scenario_plan))
        kind = plan.get("contract_version")
        if kind not in (DEMAND, PHYSICAL):
            raise SnapshotError("campaign_annotations_reviewed_source_plan_required")
        schema = "anomaly_plan.schema.json" if kind == DEMAND else "physical_plan.schema.json"
        # Reuse the closed Source1.2 schemas; do not import the native generator.
        validate_schema(plan, schema, "1.2.0")
        # Source.from_payload sorts both arrays by ID before committing identity.
        plan["injections"] = sorted(plan["injections"], key=lambda w: w["id"])
        plan["controls"] = sorted(plan["controls"], key=lambda w: w["id"])
        digest = json_sha256(plan)
        if (
            plan["seed"] != self.scope.data_seed
            or digest != descriptor["scenario_plan_sha256"]
            or digest != self.scope.source_scenario_plan_sha256
            or json_sha256(decode_json(resource_bytes(schema, "1.2.0")))
            != descriptor["scenario_schema_sha256"]
        ):
            raise SnapshotError("campaign_annotations_frozen_plan_source_hash_mismatch")
        self.plan_sha256 = digest
        identifiers: set[str] = set()
        for window in [*plan["injections"], *plan["controls"]]:
            start, end = (
                date.fromisoformat(window["start_date"]),
                date.fromisoformat(window["end_date"]),
            )
            grain = window["product_id"], window["selling_location_id"], window["channel"]
            if (
                window["id"] in identifiers
                or start > end
                or (end - start).days >= 730
                or "seed" in window
                and window["seed"] != self.scope.data_seed
                or any(
                    start <= date.fromisoformat(w["end_date"])
                    and date.fromisoformat(w["start_date"]) <= end
                    for w in self._windows[grain]
                )
            ):
                raise SnapshotError("campaign_annotations_duplicate_or_overlapping_source_windows")
            identifiers.add(window["id"])
            self._windows[grain].append(window)
        self._windows = dict(self._windows)

    def annotation(self, feature: InputRow) -> CampaignSourceKeyAnnotation:
        feature = InputRow.model_validate_json(feature.model_dump_json())
        known_offer = (
            next(v.value for v in feature.values if v.name == "planned_promotion_offered") is True
        )
        scenario: Scenario = "promotion" if known_offer else "normal"
        anomaly: Anomaly = "unannotated"
        grain = feature.product_id, feature.selling_location_id, feature.channel
        for window in self._windows.get(grain, ()):
            if window["start_date"] <= feature.target_date.isoformat() <= window["end_date"]:
                kind = window.get("injection_type")
                if kind in ("one_day_spike", "multi_day_spike", "sustained_drop"):
                    scenario, anomaly = "demand_shock", kind
                elif kind == "inventory_censored_episode":
                    scenario, anomaly = "inventory_constraint", kind
                elif kind == "return_spike":
                    anomaly = kind
                elif window.get("control_type") == "clean":
                    anomaly = "clean_control"
                # Source control windows do not invent an origin-known offer.
                # Missing/late known plans stay represented by input availability.
                break
        return CampaignSourceKeyAnnotation(
            **feature.model_dump(include=set(ForecastKey.model_fields)),
            context_scope_sha256=self.scope.content_sha256(),
            source_scenario_plan_sha256=self.plan_sha256,
            scenario=scenario,
            anomaly=anomaly,
        )
