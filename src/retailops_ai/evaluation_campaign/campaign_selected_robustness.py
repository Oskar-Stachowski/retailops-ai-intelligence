"""Full selected functional comparisons and paired diagnostics on every census group."""

from contextlib import ExitStack
from pathlib import Path
from typing import Any, Self

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
    CampaignForecastEvaluationPrediction,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_metrics import EvaluationSegment
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import CampaignPortfolioProtocol
from retailops_ai.evaluation_campaign.campaign_raw_context import context_record
from retailops_ai.evaluation_campaign.campaign_required_group_contract import (
    CampaignPortfolioRequiredGroupPolicy,
)
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignForecastKeyContext,
    CampaignForecastSegmentCensus,
)
from retailops_ai.evaluation_campaign.campaign_segments import SegmentCensus, population_ids
from retailops_ai.evaluation_campaign.campaign_uncertainty import PairedForecastUncertainty
from retailops_ai.evaluation_campaign.campaign_uncertainty_contract import (
    CampaignForecastUncertaintyPolicy,
    CampaignUncertaintyScope,
)
from retailops_ai.source_snapshot.files import SnapshotError, file_hash, read_bytes, regular_file


class SelectedRobustness:
    """One actual/context pass; disk cluster sums, no outcome/residual row list."""

    def __init__(
        self,
        root: Path,
        context_bundle: Path,
        record: dict[str, Any],
        plan: CampaignForecastEvaluationPlan,
        population: dict[str, Any],
        policy: CampaignForecastUncertaintyPolicy,
        *,
        retained_median_baseline: bool,
        required_group_policy: CampaignPortfolioRequiredGroupPolicy | None = None,
        portfolio_protocol: CampaignPortfolioProtocol | None = None,
    ) -> None:
        self.receipt, self.census = context_record(record, plan, population)
        self.policy = CampaignForecastUncertaintyPolicy.model_validate_json(
            policy.model_dump_json()
        )
        if (
            self.policy.content_sha256() != plan.uncertainty_policy_sha256
            or self.policy.nominal_interval_coverage != plan.quality_policy.nominal_coverage
            or self.census.rows > self.policy.max_rows
        ):
            raise SnapshotError("campaign_selected_robustness_policy_mismatch")
        self.root, self.bundle, self.plan = root, context_bundle, plan
        self.stream_census = SegmentCensus(self.census.scope, self.census.policy)
        self.groups = {(p.dimension, p.value): p for p in self.census.populations}
        if (required_group_policy is None) != (portfolio_protocol is None):
            raise SnapshotError("campaign_selected_robustness_required_policy_pair")
        self.required_group_policy = required_group_policy
        self.required_groups = frozenset(self.groups)
        if required_group_policy is not None and portfolio_protocol is not None:
            self.required_groups = required_group_policy.groups_for(
                portfolio_protocol, plan, self.census
            )
            if self.receipt.protocol_sha256 != portfolio_protocol.content_sha256():
                raise SnapshotError("campaign_selected_robustness_required_protocol_mismatch")
        self.metrics = {
            key: EvaluationSegment(
                key[0],
                retained_median_baseline=retained_median_baseline,
                policy=plan.quality_policy,
                max_rows=plan.max_rows,
            )
            for key in self.groups
        }
        self.paired: dict[tuple[str, str], PairedForecastUncertainty] = {}
        self.stack = ExitStack()
        self.failed = self.complete = False
        self.rows = 0
        self._seal()
        if (
            CampaignForecastSegmentCensus.model_validate_json(
                read_bytes(context_bundle, "census.json", min(plan.max_output_bytes, 16 * 1024**2))
            )
            != self.census
        ):
            raise SnapshotError("campaign_selected_robustness_census_file_mismatch")

    def _seal(self) -> None:
        for name in ("contexts.jsonl", "census.json"):
            size, digest = file_hash(self.bundle, name)
            if (
                digest != self.receipt.artifact_files[name]
                or size > self.receipt.artifact_bytes
                or size > self.receipt.recipe.max_output_bytes
            ):
                raise SnapshotError("campaign_selected_robustness_context_checksum")

    def _indexes(self) -> None:
        # Includes prepared inputs, actuals, projection and every cluster file,
        # even before SQLite has committed. No group is dropped to fit the cap.
        if sum(p.stat().st_size for p in self.root.rglob("*.sqlite")) > self.plan.max_index_bytes:
            raise SnapshotError("campaign_selected_robustness_combined_index_budget")

    def __enter__(self) -> Self:
        try:
            self.stream = self.stack.enter_context(regular_file(self.bundle, "contexts.jsonl"))
            indexes = self.root / "paired"
            indexes.mkdir(mode=0o700)
            for index, (dimension, value) in enumerate(self.groups):
                scope = CampaignUncertaintyScope.model_validate_json(
                    canonical_bytes(
                        {
                            "data_seed": self.census.scope.data_seed,
                            "role": self.plan.role,
                            "dataset_id": self.census.scope.dataset_id,
                            "source_recipe_sha256": self.plan.source_recipe_sha256,
                            "frozen_configuration_sha256": self.plan.frozen_configuration_sha256,
                            "scenario": value if dimension == "scenario" else "all",
                            "dimension": dimension,
                            "value": value,
                        }
                    )
                )
                self.paired[dimension, value] = self.stack.enter_context(
                    PairedForecastUncertainty(
                        indexes / f"group-{index:04d}.sqlite", scope, self.policy
                    )
                )
            self._indexes()
            return self
        except Exception:
            self.failed = True
            self.stack.close()
            raise

    def __exit__(self, *args: Any) -> None:
        self.stack.__exit__(*args)

    def add(self, row: CampaignForecastEvaluationPrediction, actual: int | None) -> None:
        if self.failed or self.complete:
            raise SnapshotError("campaign_selected_robustness_unavailable")
        try:
            raw = self.stream.readline(self.receipt.recipe.max_record_bytes + 1)
            if len(raw) > self.receipt.recipe.max_record_bytes or not raw.endswith(b"\n"):
                raise SnapshotError("campaign_selected_robustness_context_record_budget")
            ctx = CampaignForecastKeyContext.model_validate_json(raw)
            if (
                raw != canonical_bytes(ctx.model_dump(mode="json")) + b"\n"
                or ctx.model_dump(
                    include={
                        "product_id",
                        "selling_location_id",
                        "channel",
                        "forecast_origin",
                        "target_date",
                        "horizon_days",
                    }
                )
                != row.model_dump(
                    include={
                        "product_id",
                        "selling_location_id",
                        "channel",
                        "forecast_origin",
                        "target_date",
                        "horizon_days",
                    }
                )
                or ctx.role != row.role
                or ctx.example_sha256 != row.example_sha256
                or ctx.eligible != row.eligible
                or ctx.exclusion_reasons != row.exclusion_reasons
                or row.frozen_configuration_sha256 != self.plan.frozen_configuration_sha256
            ):
                raise SnapshotError("campaign_selected_robustness_row_context_mismatch")
            self.stream_census.add(ctx)
            self.rows += 1
            for identity in population_ids(ctx):
                self.metrics[identity].add(row, actual)
                self.paired[identity].add(row, actual)
            if self.rows % 256 == 0:
                self._indexes()
        except Exception:
            self.failed = True
            raise

    def finish(self) -> dict[str, Any]:
        if self.failed or self.complete:
            raise SnapshotError("campaign_selected_robustness_unavailable")
        try:
            if self.stream.read(1):
                raise SnapshotError("campaign_selected_robustness_extra_context_key")
            observed = self.stream_census.finish(
                expected_rows=self.census.rows,
                expected_eligible_rows=self.census.eligible_rows,
                expected_keys_sha256=self.census.keys_sha256,
                expected_eligible_keys_sha256=self.census.eligible_keys_sha256,
            )
            if observed != self.census:
                raise SnapshotError("campaign_selected_robustness_full_census_mismatch")
            results = []
            for identity, population in self.groups.items():
                paired = self.paired[identity].report(
                    expected_rows=population.rows,
                    expected_eligible_rows=population.eligible_rows,
                    expected_keys_sha256=population.keys_sha256,
                    expected_eligible_keys_sha256=population.eligible_keys_sha256,
                )
                results.append(
                    {
                        **population.model_dump(mode="json"),
                        "comparison": self.metrics[identity].result(),
                        "uncertainty": paired.model_dump(mode="json"),
                    }
                )
            self._seal()
            self._indexes()
            self.complete = True
            report = {
                "context_receipt_sha256": self.receipt.content_sha256(),
                "context_census_sha256": self.census.content_sha256(),
                "context_trace_sha256": self.census.context_trace_sha256,
                "uncertainty_policy_sha256": self.policy.content_sha256(),
                "all_declared_groups_consumed": True,
                "quality_qualified": all(
                    r["comparison"]["status"] == "passed"
                    for r in results
                    if (r["dimension"], r["value"]) in self.required_groups
                ),
                "groups": results,
            }
            if self.required_group_policy is not None:
                report.update(
                    required_group_policy_sha256=self.required_group_policy.content_sha256(),
                    required_groups=[
                        {"dimension": dimension, "value": value}
                        for dimension, value in sorted(self.required_groups)
                    ],
                )
            return report
        except Exception:
            self.failed = True
            raise
