"""One bounded physical batch; replay verification before atomic DB publication."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, RunID, TrueFlag, UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.stockout_jobs.contracts import OutputID, ProfileID, StockoutRun
from retailops_ai.stockout_lifecycle.contract import StockoutModelRelease
from retailops_ai.stockout_runtime.contracts import RiskItem
from retailops_ai.stockout_runtime.inputs import PreparedStockoutInputs
from retailops_ai.stockout_runtime.scoring import score_point

MAX_OUTPUT_BYTES = 4 * 1024**2


class StockoutOutput(Contract):
    version: Literal["stockout-batch-output-1.0.0"] = "stockout-batch-output-1.0.0"
    output_id: OutputID
    complete: TrueFlag = True
    run_id: RunID
    profile_id: ProfileID
    release: StockoutModelRelease
    generated_at: UtcTime
    items: tuple[RiskItem, ...] = Field(min_length=1, max_length=100)
    model_refits: Annotated[int, Field(ge=0, le=0)] = 0
    source_generation: FalseFlag = False

    @model_validator(mode="after")
    def coherent(self) -> Self:
        pairs = [(r.product_id, r.stock_location_id) for r in self.items]
        q = self.release.binding.approval.qualification
        if (
            pairs != sorted(set(pairs))
            or any(
                (
                    r.inference_run_id,
                    r.release_id,
                    r.generated_at,
                    r.model_name,
                    r.model_version,
                    r.calibrator_version,
                    r.threshold_version,
                )
                != (
                    self.run_id,
                    self.release.release_id,
                    self.generated_at,
                    self.release.binding.model_name,
                    self.release.binding.model_version,
                    "stockout-calibrator-sha256-" + q.recipe.pin.calibrator_sha256,
                    q.policy.policy_id,
                )
                for r in self.items
            )
            or self.output_id
            != "stockout-output-sha256-"
            + canonical_sha256(self.model_dump(mode="json", exclude={"output_id"}))
            or len(canonical_bytes(self.model_dump(mode="json"))) > MAX_OUTPUT_BYTES
        ):
            raise ValueError("stockout_batch_output_identity_binding_or_limit")
        return self


def check_inputs(
    run: StockoutRun, inputs: PreparedStockoutInputs, release: StockoutModelRelease
) -> PreparedStockoutInputs:
    inputs = PreparedStockoutInputs.model_validate_json(inputs.model_dump_json())
    if (
        run.release != release
        or run.input_ref.request.profile_id != inputs.inputs_id
        or run.input_ref.request.as_of != inputs.as_of
        or run.input_ref.lineage != inputs.lineage
        or set(run.input_ref.request.scope.product_ids) != set(inputs.scope.product_ids)
        or set(run.input_ref.request.scope.stock_location_ids)
        != set(inputs.scope.stock_location_ids)
    ):
        raise ValueError("stockout_batch_input_or_release_pin")
    return inputs


def compute(
    run: StockoutRun,
    inputs: PreparedStockoutInputs,
    release: StockoutModelRelease,
    *,
    generated_at: datetime,
    tick: Callable[[], None] | None = None,
) -> StockoutOutput:
    run = StockoutRun.model_validate_json(run.model_dump_json())
    release = StockoutModelRelease.model_validate_json(release.model_dump_json())
    inputs = check_inputs(run, inputs, release)
    if run.status != "running" or run.started_at is None or generated_at < run.started_at:
        raise ValueError("stockout_batch_running_attempt_required")
    q = release.binding.approval.qualification
    rows = []
    for point in inputs.points:
        if tick:
            tick()
        rows.append(
            score_point(
                point.feature,
                category_id=point.category_id,
                category_available_at=point.category_available_at,
                upstream=point.upstream,
                recipe=q.recipe,
                policy=q.policy,
                release=release.runtime_pin(),
                lineage=inputs.lineage,
                run_id=run.run_id,
                generated_at=generated_at,
            ).model_dump(mode="json")
        )
    raw = dict(
        version="stockout-batch-output-1.0.0",
        complete=True,
        run_id=run.run_id,
        profile_id=inputs.inputs_id,
        release=release.model_dump(mode="json"),
        generated_at=generated_at.isoformat(),
        items=rows,
        model_refits=0,
        source_generation=False,
    )
    # Normalize UTC serialization before computing the identity.
    raw["generated_at"] = rows[0]["generated_at"]
    raw["output_id"] = "stockout-output-sha256-" + canonical_sha256(raw)
    return StockoutOutput.model_validate_json(canonical_bytes(raw))


def verify_output(
    run: StockoutRun,
    inputs: PreparedStockoutInputs,
    release: StockoutModelRelease,
    output: StockoutOutput,
    *,
    tick: Callable[[], None] | None = None,
) -> None:
    output = StockoutOutput.model_validate_json(output.model_dump_json())
    if output != compute(run, inputs, release, generated_at=output.generated_at, tick=tick):
        raise ValueError("stockout_batch_complete_replay_mismatch")
