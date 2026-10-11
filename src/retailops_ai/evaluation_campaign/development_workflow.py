"""Connect original Source and forecast controllers; retain every original journal and cost."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignDevelopmentExportPlan
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitPlan
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import VARIANTS
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScorePlan
from retailops_ai.evaluation_campaign.canonical_development_contract import (
    PreparedCanonicalDevelopmentParents,
)
from retailops_ai.evaluation_campaign.canonical_forecast import compile_canonical_forecast
from retailops_ai.evaluation_campaign.canonical_forecast_contract import CompletedCanonicalForecast
from retailops_ai.evaluation_campaign.canonical_qualification import (
    PreparedCanonicalQualificationProtocol,
    compile_canonical_qualification,
    qualification_protocol,
)
from retailops_ai.evaluation_campaign.canonical_qualification_execution_contract import (
    CanonicalQualificationExecutionResult,
)
from retailops_ai.evaluation_campaign.development_advancement import (
    compile_forecast_advancement,
    freeze_multiscale_selection,
)
from retailops_ai.evaluation_campaign.development_advancement_contract import (
    CompletedForecastAdvancement,
    CompletedForecastScreening,
    ForecastScaleBudget,
    MultiscaleSelectionProtocol,
)
from retailops_ai.evaluation_campaign.development_execution import (
    DevelopmentExecutionPaths,
    DevelopmentParentPaths,
    Protocol,
    run_development_forecast,
)
from retailops_ai.evaluation_campaign.development_policy_freeze import FrozenDevelopmentPolicies
from retailops_ai.evaluation_campaign.development_screening import (
    compile_prepared_development_screening,
)
from retailops_ai.evaluation_campaign.development_screening_contract import (
    PreparedDevelopmentParents,
)
from retailops_ai.evaluation_campaign.development_screening_selection_contract import (
    freeze_screening_selection,
)
from retailops_ai.evaluation_campaign.development_workflow_contract import (
    DevelopmentWorkflowOptions,
    QualifiedDevelopmentWorkflowOptions,
    parse_development_workflow_options,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.preparation_execution import locked, write_once
from retailops_ai.evaluation_campaign.source_preparation import (
    inspect_preparation_producer,
    run_source_preparation,
)
from retailops_ai.evaluation_campaign.source_preparation_contract import (
    SourcePreparationProducerPin,
    SourcePreparationSession,
)
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.source_snapshot.files import SnapshotError, checked_directory, read_bytes
from retailops_ai.tensorflow_challenger.pipeline import environment_lock

MAX_BYTES = 4 * 1024**2


def _same(path: Path, value: Mapping[str, object]) -> None:
    if read_bytes(path.parent, path.name, MAX_BYTES) != canonical_bytes(dict(value)) + b"\n":
        raise SnapshotError("development_workflow_original_document_changed")


def _freeze(
    root: Path, options: DevelopmentWorkflowOptions, policies: FrozenDevelopmentPolicies
) -> SourcePreparationProducerPin:
    producer = Path(options.source.producer).resolve()
    if (
        root != root.resolve()
        or root.is_relative_to(producer)
        or producer.is_relative_to(root)
        or policies.context.runtime != runtime_pin()
        or options.worker_environment_lock_sha256 != hashlib.sha256(environment_lock()).hexdigest()
    ):
        raise SnapshotError("development_workflow_path_runtime_or_worker_lock_changed")
    path = root / "development-workflow.json"
    value = {
        "version": (
            "ai09-qualified-development-workflow-1.0.0"
            if isinstance(options, QualifiedDevelopmentWorkflowOptions)
            else "ai09-original-development-workflow-1.0.0"
        ),
        "root": str(root),
        "options": options.model_dump(mode="json"),
        "policies": policies.model_dump(mode="json"),
    }
    if path.exists() or path.is_symlink():
        observed = json.loads(read_bytes(root, path.name, MAX_BYTES))
        pin = SourcePreparationProducerPin.model_validate(observed["producer_pin"])
        _same(path, {**value, "producer_pin": pin.model_dump(mode="json")})
        return pin
    if any(p.name != "execution.lock" for p in root.iterdir()):
        raise SnapshotError("development_workflow_must_precede_all_operations")
    pin = inspect_preparation_producer(options.source, policies)
    write_once(path, {**value, "producer_pin": pin.model_dump(mode="json")})
    return pin


def _source_root(root: Path, products: int) -> Path:
    path = root / str(products) / "source"
    marker = root / f"source-{products}-initialization.json"
    value = {"source_root": str(path)}
    if marker.exists() or marker.is_symlink():
        _same(marker, value)
        if not (path / "source-preparation.json").is_file():
            raise SnapshotError("development_workflow_lost_source_session_cannot_reset")
    else:
        if path.parent.exists() or path.parent.is_symlink():
            raise SnapshotError("development_workflow_cannot_adopt_existing_scale")
        write_once(marker, value)
        path.parent.mkdir(mode=0o700)
        path.mkdir(mode=0o700)
    return checked_directory(path)


def _budget(
    root: Path, options: DevelopmentWorkflowOptions, products: Literal["25", "50", "100"]
) -> ForecastScaleBudget:
    resource = options.resources[products]
    return ForecastScaleBudget(
        bootstrap_journal_path=str(root / products / "source/planning"),
        preparation_journal_path=str(root / products / "source/variants"),
        execution_journal_path=str(root / products / "forecast"),
        preparation=options.source.generation_resources,
        fit=resource.fit,
        score=resource.score,
        maximum_total_preparation_wall_seconds=options.source.maximum_total_wall_seconds,
    )


def _screening(
    root: Path, options: DevelopmentWorkflowOptions, parents: PreparedDevelopmentParents
) -> MultiscaleSelectionProtocol:
    partitions = options.source.partitions
    exports = tuple(
        CampaignDevelopmentExportPlan(
            source_recipe_sha256=source.content_sha256(),
            generation_operation_id=generated.operation_id,
            origins=OriginWindow(
                start=partitions.roles[0].origins.start, end=partitions.roles[-1].origins.end
            ),
            **partitions.model_dump(include={"roles", "purge_days", "label_delay_days"}),
        )
        for source, generated in zip(parents.preparation.sources, parents.generations, strict=True)
    )
    source_sha = parents.preparation.sources[0].content_sha256()
    fits = tuple(
        CampaignForecastFitPlan(
            source_recipe_sha256=source_sha,
            export_operation_id="development-25-ordinary-export",
            initialization_seed=42,
            worker_environment_lock_sha256=options.worker_environment_lock_sha256,
            resources=options.resources["25"].fit,
            **recipe.model_dump(exclude={"name"}),
        )
        for recipe in options.recipes
    )
    scores = tuple(
        CampaignForecastScorePlan(
            source_recipe_sha256=source_sha,
            export_operation_id="development-25-ordinary-export",
            fit_operation_ids={
                f: f"development-25-{f}-trial-{i}" for f in ("rf", "hgb", "tensorflow")
            },
            role="tune",
            worker_environment_lock_sha256=options.worker_environment_lock_sha256,
            resources=options.resources["25"].score,
        )
        for i in (1, 2)
    )
    prefix = compile_prepared_development_screening(
        root / "25/forecast",
        parents=parents,
        exports=exports,
        fits=fits,
        scores=(scores[0], scores[1]),
    )
    return freeze_multiscale_selection(
        freeze_screening_selection(prefix),
        fifty=_budget(root, options, "50"),
        hundred=_budget(root, options, "100"),
    )


def _forecast(
    root: Path,
    options: DevelopmentWorkflowOptions,
    products: int,
    parents: PreparedDevelopmentParents | PreparedCanonicalDevelopmentParents,
    previous: CompletedForecastScreening
    | CompletedForecastAdvancement
    | CompletedCanonicalForecast
    | CanonicalQualificationExecutionResult
    | None,
    *,
    policies: FrozenDevelopmentPolicies,
) -> Protocol:
    path = root / str(products) / "forecast"
    marker = root / f"forecast-{products}-initialization.json"
    protocol: Protocol
    if marker.exists() or marker.is_symlink():
        if not (path / "journal.json").is_file():
            raise SnapshotError("development_workflow_lost_forecast_journal_cannot_reset")
        from retailops_ai.evaluation_campaign.development_execution import _protocol

        protocol = _protocol(campaign_journal.inspect(path))
        if isinstance(protocol, PreparedCanonicalQualificationProtocol) != (
            products == 100 and isinstance(options, QualifiedDevelopmentWorkflowOptions)
        ):
            raise SnapshotError("development_workflow_qualification_requires_frozen_program")
        _same(marker, {"protocol_sha256": protocol.content_sha256()})
        observed = (
            protocol.screening.parents
            if isinstance(protocol, MultiscaleSelectionProtocol)
            else protocol.qualification.prefix.parents
            if isinstance(protocol, PreparedCanonicalQualificationProtocol)
            else protocol.parents
        )
        if observed != parents or protocol.journal_path != str(path):
            raise SnapshotError("development_workflow_original_forecast_parents_changed")
        return protocol
    if path.exists() or path.is_symlink():
        raise SnapshotError("development_workflow_cannot_adopt_forecast_journal")
    if products == 25 and isinstance(parents, PreparedDevelopmentParents) and previous is None:
        protocol = _screening(root, options, parents)
    elif (
        products == 50
        and isinstance(parents, PreparedDevelopmentParents)
        and isinstance(previous, CompletedForecastScreening)
    ):
        protocol = compile_forecast_advancement(screening=previous, parents=parents)
    elif (
        products == 100
        and isinstance(parents, PreparedCanonicalDevelopmentParents)
        and isinstance(previous, CompletedForecastAdvancement)
    ):
        protocol = (
            qualification_protocol(
                compile_canonical_qualification(
                    advancement=previous, parents=parents, policies=policies
                )
            )
            if isinstance(options, QualifiedDevelopmentWorkflowOptions)
            else compile_canonical_forecast(advancement=previous, parents=parents)
        )
    else:
        raise SnapshotError("development_workflow_original_scale_order_required")
    write_once(marker, {"protocol_sha256": protocol.content_sha256()})
    campaign_journal.initialize(path, protocol)
    return protocol


def _counts(result: dict[str, object]) -> tuple[int, int]:
    new, completed = result["new_operations"], result["completed_operations"]
    if type(new) is not int or type(completed) is not int or not 0 <= new <= completed <= 43:
        raise SnapshotError("development_workflow_original_operation_counts_required")
    return new, completed


def run_development_workflow(
    root: Path,
    options: DevelopmentWorkflowOptions,
    policies: FrozenDevelopmentPolicies,
    *,
    maximum_new_operations: int | None = None,
) -> dict[str, object]:
    """Execute the frozen 43- or 51-operation program, retaining the same 12 model fits."""
    options = parse_development_workflow_options(options.model_dump_json())
    planned = options.maximum_source_operations + options.maximum_forecast_operations
    maximum_new_operations = planned if maximum_new_operations is None else maximum_new_operations
    if type(maximum_new_operations) is not int or not 1 <= maximum_new_operations <= planned:
        raise SnapshotError("development_workflow_operation_limit")
    policies = FrozenDevelopmentPolicies.model_validate_json(policies.model_dump_json())
    root = root.absolute()
    new = completed = 0
    summary: dict[str, object] = {
        "status": "incomplete",
        "new_operations": 0,
        "completed_operations": 0,
        "planned_operations": planned,
        "quality_qualified": False,
        "final_test_access_authorized": False,
        "stage_ready": False,
    }
    previous: (
        CompletedForecastScreening
        | CompletedForecastAdvancement
        | CompletedCanonicalForecast
        | CanonicalQualificationExecutionResult
        | None
    ) = None
    with locked(root):
        pin = _freeze(root, options, policies)
        for products in (25, 50, 100):
            source_root = _source_root(root, products)
            source_options = options.source.model_copy(update={"products": products})
            prepared = run_source_preparation(
                source_root,
                source_options,
                policies,
                maximum_new_operations=min(5, maximum_new_operations - new),
            )
            session = SourcePreparationSession.model_validate_json(
                read_bytes(source_root, "source-preparation.json", MAX_BYTES)
            )
            if session.producer_pin != pin:
                raise SnapshotError("development_workflow_original_source_pin_changed")
            source_new, source_completed = _counts(prepared)
            new += source_new
            completed += source_completed
            summary.update(new_operations=new, completed_operations=completed)
            if not prepared["complete"] or new == maximum_new_operations:
                return summary
            value = json.loads(read_bytes(source_root, "source-preparation-result.json", MAX_BYTES))
            kind = (
                PreparedCanonicalDevelopmentParents
                if products == 100
                else PreparedDevelopmentParents
            )
            parents = kind.model_validate_json(canonical_bytes(value["parents"]))
            protocol = _forecast(root, options, products, parents, previous, policies=policies)
            output = root / str(products) / "forecast-artifacts"
            output.mkdir(mode=0o700, exist_ok=True)
            paths = DevelopmentExecutionPaths(
                protocol_sha256=protocol.content_sha256(),
                parents=tuple(
                    DevelopmentParentPaths(
                        **{k: value["parent_paths"][v][k] for k in ("snapshot", "curated")}
                    )
                    for v in VARIANTS
                ),  # type: ignore[arg-type]
                worker_python=options.worker_python,
                output_root=str(output),
            )
            result = run_development_forecast(
                root / str(products) / "forecast",
                paths,
                maximum_new_operations=min(
                    16 if isinstance(protocol, PreparedCanonicalQualificationProtocol) else 12,
                    maximum_new_operations - new,
                ),
            )
            forecast_new, forecast_completed = _counts(result)
            new += forecast_new
            completed += forecast_completed
            summary.update(new_operations=new, completed_operations=completed)
            qualified = isinstance(protocol, PreparedCanonicalQualificationProtocol)
            if result["status"] != "completed" and not (
                qualified and result["status"] == "not_ready"
            ):
                return summary
            raw = read_bytes(
                root / str(products) / "forecast",
                "qualification-execution-result.json"
                if qualified
                else "forecast-execution-result.json",
                MAX_BYTES,
            )
            result_kind = (
                CompletedForecastScreening
                if products == 25
                else CompletedForecastAdvancement
                if products == 50
                else CanonicalQualificationExecutionResult
                if qualified
                else CompletedCanonicalForecast
            )
            previous = result_kind.model_validate_json(raw)
            if (
                isinstance(previous, CompletedForecastAdvancement)
                and previous.status != "original_families_verified"
            ):
                raise SnapshotError("development_workflow_fifty_not_ready_no_canonical_generation")
            if products < 100 and new == maximum_new_operations:
                return summary
        if not isinstance(
            previous, (CompletedCanonicalForecast, CanonicalQualificationExecutionResult)
        ):
            raise SnapshotError("development_workflow_original_canonical_result_required")
        value = {
            "version": (
                "ai09-qualified-development-workflow-result-1.0.0"
                if isinstance(previous, CanonicalQualificationExecutionResult)
                else "ai09-original-development-workflow-result-1.0.0"
            ),
            "workflow_sha256": canonical_sha256(
                json.loads(read_bytes(root, "development-workflow.json", MAX_BYTES))
            ),
            "canonical_result": previous.model_dump(mode="json"),
            "completed_operations": completed,
            "quality_qualified": False,
            "final_test_access_authorized": False,
            "stage_ready": False,
        }
        path = root / "development-workflow-result.json"
        if path.exists() or path.is_symlink():
            _same(path, value)
        else:
            write_once(path, value)
        if isinstance(previous, CanonicalQualificationExecutionResult):
            summary.update(
                status=previous.status,
                development_quality_qualified=previous.development_quality_qualified,
                reasons=list(previous.reasons),
            )
        else:
            summary["status"] = (
                "completed" if previous.status == "original_families_verified" else "not_ready"
            )
        return summary
