"""Reserve the whole Source and role before supervised context materialization."""

import json
import os
import stat
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import JsonValue, TypeAdapter

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_context_bundle_contract import (
    FILES,
    CampaignContextBundleReceipt,
    CampaignContextBundleRecipe,
)
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignOperationPlan,
)
from retailops_ai.evaluation_campaign.campaign_export import (
    MAX_RECEIPT_BYTES,
    _store_receipt,
    validate_completed_export,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportReceipt,
    CampaignGeneratedParentReceipt,
)
from retailops_ai.evaluation_campaign.campaign_final_contract import CampaignFinalExportReceipt
from retailops_ai.evaluation_campaign.campaign_final_export import validate_completed_final_export
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation import (
    _environment,
    validate_completed_generation,
)
from retailops_ai.evaluation_campaign.campaign_generation_contract import CampaignGenerationPlan
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignForecastContextScope,
    CampaignForecastKeyContext,
    CampaignForecastSegmentCensus,
)
from retailops_ai.evaluation_campaign.campaign_segments import SegmentCensus
from retailops_ai.evaluation_campaign.campaign_selection_evidence import (
    verify_completed_campaign_selection,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree

Export = CampaignDevelopmentExportReceipt | CampaignFinalExportReceipt


def _operation(ledger: CampaignJournal, operation_id: str) -> CampaignOperationPlan:
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if operation is None or (operation.action, operation.use_case, operation.role) != (
        "source_read",
        "source",
        "all_parent_data",
    ):
        raise SnapshotError("campaign_context_bundle_requires_whole_parent_read")
    return operation


def _binding(
    ledger: CampaignJournal,
    operation: CampaignOperationPlan,
    recipe: CampaignContextBundleRecipe,
    generation: CampaignGenerationPlan,
    generated: CampaignGeneratedParentReceipt,
    exported: Export,
) -> None:
    operations = {o.operation_id: o for o in ledger.protocol.operations}
    parent = operations.get(recipe.generation_operation_id)
    export = operations.get(recipe.export_operation_id)
    source = next(
        (s for s in ledger.protocol.sources if s.content_sha256() == recipe.source_recipe_sha256),
        None,
    )
    if (
        source is None
        or parent is None
        or export is None
        or operation.execution_recipe_sha256 != recipe.content_sha256()
        or operation.phase != recipe.phase
        or operation.source_recipe_sha256 != recipe.source_recipe_sha256
        or recipe.segment_policy.content_sha256() != ledger.protocol.segment_policy_sha256
        or not {recipe.generation_operation_id, recipe.export_operation_id}
        <= set(operation.prerequisites)
        or parent.action != "source_generate"
        or parent.phase != recipe.phase
        or parent.execution_recipe_sha256 != generation.content_sha256()
        or parent.source_recipe_sha256 != recipe.source_recipe_sha256
        or (export.action, export.use_case, export.role)
        != ("source_read", "source", "all_parent_data")
        or export.phase != recipe.phase
        or export.source_recipe_sha256 != recipe.source_recipe_sha256
        or generated.operation_id != recipe.generation_operation_id
        or generated.source_recipe_sha256 != recipe.source_recipe_sha256
        or exported.operation_id != recipe.export_operation_id
        or exported.plan.generation_operation_id != recipe.generation_operation_id
        or exported.plan.source_recipe_sha256 != recipe.source_recipe_sha256
        or exported.generated_parent_receipt_sha256 != generated.content_sha256()
        or exported.recipe.source != generated.source
        or generated.protocol_sha256 != ledger.protocol_sha256
        or exported.protocol_sha256 != ledger.protocol_sha256
        or generated.runtime != ledger.protocol.runtime
        or generation.snapshot_schema_version != generated.source.schema_version
        or generation.parent_budget.model_dump()
        != {k: getattr(generated.source, k) for k in type(generation.parent_budget).model_fields}
        or exported.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
        or isinstance(exported, CampaignFinalExportReceipt) != (recipe.phase == "final")
    ):
        raise SnapshotError("campaign_context_bundle_frozen_parent_binding_mismatch")
    generation.bind(source)


def build_campaign_context_bundle(
    snapshot: Path,
    curated: Path,
    dataset: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    recipe: CampaignContextBundleRecipe,
    generation: CampaignGenerationPlan,
    generated: CampaignGeneratedParentReceipt,
    exported: Export,
    selection_bundles: dict[str, Path] | None = None,
) -> tuple[Path, CampaignContextBundleReceipt]:
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, operation_id)
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        recipe = CampaignContextBundleRecipe.model_validate_json(recipe.model_dump_json())
        generation = CampaignGenerationPlan.model_validate_json(generation.model_dump_json())
        generated = CampaignGeneratedParentReceipt.model_validate_json(generated.model_dump_json())
        exported = (
            CampaignFinalExportReceipt
            if recipe.phase == "final"
            else CampaignDevelopmentExportReceipt
        ).model_validate_json(exported.model_dump_json())
        _binding(ledger, operation, recipe, generation, generated, exported)
        validate_completed_generation(journal, generated)
        if isinstance(exported, CampaignFinalExportReceipt):
            validate_completed_final_export(journal, exported)
            selection, _ = verify_completed_campaign_selection(journal, selection_bundles or {})
        else:
            validate_completed_export(journal, exported)
            selection = None
        checked_directory(output_root)
        output = output_root.resolve()
        for parent in (snapshot, curated, dataset):
            resolved = parent.resolve()
            if output.is_relative_to(resolved) or resolved.is_relative_to(output):
                raise SnapshotError("campaign_context_bundle_distinct_output_and_parents_required")
        if stat.S_IMODE(output_root.stat().st_mode) != 0o700:
            raise SnapshotError("campaign_context_bundle_private_output_required")
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        source = next(
            s for s in ledger.protocol.sources if s.content_sha256() == recipe.source_recipe_sha256
        )
        write(
            root / "request.json",
            {
                "runtime": ledger.protocol.runtime.model_dump(mode="json"),
                "recipe": recipe.model_dump(mode="json"),
                "generation": generation.model_dump(mode="json"),
                "generated": generated.model_dump(mode="json"),
                "exported": exported.model_dump(mode="json"),
                "source_recipe": source.model_dump(mode="json"),
                "snapshot": str(snapshot.absolute()),
                "curated": str(curated.absolute()),
                "dataset": str(dataset.absolute()),
            },
        )
        measured = monitor(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).with_name("campaign_context_bundle_entry.py")),
                str(root),
            ],
            root=root,
            log=root / "context.log",
            env=_environment(root),
            scratch=(root,),
            resources=recipe.resources,
            deadline=started + recipe.resources.wall_seconds,
        )
        peak = measured.get("sampled_tree_peak_rss_bytes")
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
        )
        write(root / "resources.json", measured)
        if measured["status"] != "passed":
            raise SnapshotError("campaign_context_bundle_worker_failed")
        result = read(root / "result.json")
        worker_peak = result.get("worker_peak_rss_bytes")
        if type(worker_peak) is not int or worker_peak <= 0:
            raise SnapshotError("campaign_context_bundle_worker_resource_evidence_missing")
        peak = max(peak or 0, worker_peak)
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
        )
        if peak > recipe.resources.tree_rss_bytes:
            raise SnapshotError("campaign_context_bundle_worker_peak_rss_limit")
        population = result["population"]
        receipt = CampaignContextBundleReceipt(
            protocol_sha256=ledger.protocol_sha256,
            operation_id=operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            recipe=recipe,
            generated_parent_receipt_sha256=generated.content_sha256(),
            generation_plan_sha256=generation.content_sha256(),
            export_receipt_sha256=exported.content_sha256(),
            runtime_code_sha256=ledger.protocol.runtime.code_sha256,
            scope=CampaignForecastContextScope.model_validate_json(
                canonical_bytes(result["scope"])
            ),
            **{
                k: population[k]
                for k in (
                    "rows",
                    "eligible_rows",
                    "keys_sha256",
                    "eligible_keys_sha256",
                    "role_population_sha256",
                )
            },
            **{
                k: result[k]
                for k in (
                    "context_trace_sha256",
                    "census_sha256",
                    "snapshot_inventory_sha256",
                    "curated_inventory_sha256",
                    "logical_curated_sha256",
                    "artifact_sha256",
                    "artifact_bytes",
                    "artifact_files",
                    "full_role_label_file_passes",
                    "complete_export_role_file_passes",
                )
            },
            selection_sha256=selection,
            worker_evidence=TypeAdapter(dict[str, JsonValue]).validate_python(
                {"monitor": measured, "worker": result}
            ),
        )
        bundle = root / "bundle"
        _verify_contents(bundle, receipt)
        fsync_tree(bundle)
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=receipt.artifact_bytes,
        )
    verify_campaign_context_bundle(
        bundle, journal=journal, receipt=receipt, selection_bundles=selection_bundles
    )
    return bundle, receipt


def _verify_contents(bundle: Path, receipt: CampaignContextBundleReceipt) -> None:
    hashes, size = _bundle_inventory(bundle, receipt.recipe.max_output_bytes)
    if (hashes, size) != (receipt.artifact_files, receipt.artifact_bytes) or set(hashes) != FILES:
        raise SnapshotError("campaign_context_bundle_artifact_inventory_mismatch")

    def document(name: str) -> dict[str, Any]:
        value = json.loads(
            read_bytes(bundle, name, min(receipt.recipe.max_output_bytes, 16 * 1024**2))
        )
        if not isinstance(value, dict):
            raise SnapshotError("campaign_context_bundle_metadata_schema")
        return value

    if (
        CampaignContextBundleRecipe.model_validate_json(canonical_bytes(document("recipe.json")))
        != receipt.recipe
        or CampaignForecastContextScope.model_validate_json(canonical_bytes(document("scope.json")))
        != receipt.scope
    ):
        raise SnapshotError("campaign_context_bundle_artifact_scope_mismatch")
    parents = document("parents.json")
    generation = CampaignGenerationPlan.model_validate_json(canonical_bytes(parents["generation"]))
    generated = CampaignGeneratedParentReceipt.model_validate_json(
        canonical_bytes(parents["generated"])
    )
    exported = (
        CampaignFinalExportReceipt
        if receipt.recipe.phase == "final"
        else CampaignDevelopmentExportReceipt
    ).model_validate_json(canonical_bytes(parents["exported"]))
    if (
        generation.content_sha256() != receipt.generation_plan_sha256
        or generated.content_sha256() != receipt.generated_parent_receipt_sha256
        or exported.content_sha256() != receipt.export_receipt_sha256
        or exported.dataset_id != receipt.scope.dataset_id
        or generated.source.parent.source_dataset_id != receipt.scope.source_dataset_id
        or generated.source.parent.snapshot_id != receipt.scope.snapshot_id
        or generated.source.parent.curated_dataset_id != receipt.scope.curated_dataset_id
        or generated.source.source_parameters["seed"] != receipt.scope.data_seed
    ):
        raise SnapshotError("campaign_context_bundle_artifact_parent_mismatch")
    population, seal = document("population.json"), document("source-seal.json")
    if (
        any(
            population[k] != getattr(receipt, k)
            for k in (
                "rows",
                "eligible_rows",
                "keys_sha256",
                "eligible_keys_sha256",
                "role_population_sha256",
            )
        )
        or population["role"] != receipt.scope.role
        or population["dataset_id"] != receipt.scope.dataset_id
    ):
        raise SnapshotError("campaign_context_bundle_artifact_population_mismatch")
    if (
        any(
            seal[k] != getattr(receipt, k)
            for k in (
                "snapshot_inventory_sha256",
                "curated_inventory_sha256",
                "logical_curated_sha256",
            )
        )
        or seal["source_scenario_plan_sha256"] != receipt.scope.source_scenario_plan_sha256
    ):
        raise SnapshotError("campaign_context_bundle_artifact_source_seal_mismatch")
    census = SegmentCensus(receipt.scope, receipt.recipe.segment_policy)
    with regular_file(bundle, "contexts.jsonl") as stream:
        while raw := stream.readline(receipt.recipe.max_record_bytes + 1):
            if len(raw) > receipt.recipe.max_record_bytes or not raw.endswith(b"\n"):
                raise SnapshotError("campaign_context_bundle_context_record_budget")
            row = CampaignForecastKeyContext.model_validate_json(raw)
            if canonical_bytes(row.model_dump(mode="json")) + b"\n" != raw:
                raise SnapshotError("campaign_context_bundle_context_noncanonical")
            census.add(row)
    complete = census.finish(
        **{
            "expected_" + k: getattr(receipt, k)
            for k in ("rows", "eligible_rows", "keys_sha256", "eligible_keys_sha256")
        }
    )
    observed = CampaignForecastSegmentCensus.model_validate_json(
        canonical_bytes(document("census.json"))
    )
    if (
        observed != complete
        or complete.context_trace_sha256 != receipt.context_trace_sha256
        or observed.model_dump() != complete.model_dump()
    ):
        raise SnapshotError("campaign_context_bundle_census_mismatch")
    from retailops_ai.data_contracts.identity import canonical_sha256

    if canonical_sha256(complete.model_dump(mode="json")) != receipt.census_sha256:
        raise SnapshotError("campaign_context_bundle_census_digest_mismatch")


def verify_campaign_context_bundle(
    bundle: Path,
    *,
    journal: Path,
    receipt: CampaignContextBundleReceipt,
    selection_bundles: dict[str, Path] | None = None,
) -> CampaignContextBundleReceipt:
    """A typed header alone is insufficient; verify durable completion and every row."""
    receipt = CampaignContextBundleReceipt.model_validate_json(receipt.model_dump_json())
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, receipt.operation_id)
    completion = next(
        (
            e
            for e in ledger.events
            if e.kind == "finished" and e.reservation_id == receipt.reservation_id
        ),
        None,
    )
    if (
        receipt.protocol_sha256 != ledger.protocol_sha256
        or operation.execution_recipe_sha256 != receipt.recipe.content_sha256()
        or operation.source_recipe_sha256 != receipt.recipe.source_recipe_sha256
        or operation.phase != receipt.recipe.phase
        or ledger.protocol.runtime.code_sha256 != receipt.runtime_code_sha256
        or completion is None
        or completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
        or completion.cost is None
        or completion.cost.artifact_bytes != receipt.artifact_bytes
    ):
        raise SnapshotError("campaign_context_bundle_receipt_not_completed")
    with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
        if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise SnapshotError("campaign_context_bundle_private_receipt_required")
        raw = stream.read(MAX_RECEIPT_BYTES + 1)
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("campaign_context_bundle_stored_receipt_mismatch")
    if receipt.recipe.phase == "final":
        selection, _ = verify_completed_campaign_selection(journal, selection_bundles or {})
        if selection != receipt.selection_sha256:
            raise SnapshotError("campaign_context_bundle_selection_mismatch")
    _verify_contents(bundle, receipt)
    parents = read(bundle / "parents.json")
    generation = CampaignGenerationPlan.model_validate_json(canonical_bytes(parents["generation"]))
    generated = CampaignGeneratedParentReceipt.model_validate_json(
        canonical_bytes(parents["generated"])
    )
    exported = (
        CampaignFinalExportReceipt
        if receipt.recipe.phase == "final"
        else CampaignDevelopmentExportReceipt
    ).model_validate_json(canonical_bytes(parents["exported"]))
    _binding(ledger, operation, receipt.recipe, generation, generated, exported)
    validate_completed_generation(journal, generated)
    if isinstance(exported, CampaignFinalExportReceipt):
        validate_completed_final_export(journal, exported)
    else:
        validate_completed_export(journal, exported)
    return receipt
