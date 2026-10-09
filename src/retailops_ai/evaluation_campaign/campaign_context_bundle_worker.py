"""Isolated whole-source/full-role materialization, never model inference."""

import os
import sys
from contextlib import closing
from pathlib import Path
from time import perf_counter
from typing import Any, cast, get_args

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_evaluation_data as data
from retailops_ai.evaluation_campaign.campaign_context_bundle_contract import (
    CampaignContextBundleRecipe,
)
from retailops_ai.evaluation_campaign.campaign_context_facts import CampaignContextFacts
from retailops_ai.evaluation_campaign.campaign_contract import CampaignSourceRecipe
from retailops_ai.evaluation_campaign.campaign_export import _producer
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportReceipt,
    CampaignGeneratedParentReceipt,
)
from retailops_ai.evaluation_campaign.campaign_final_contract import CampaignFinalExportReceipt
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation_contract import CampaignGenerationPlan
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_segment_contract import CampaignForecastContextScope
from retailops_ai.evaluation_campaign.campaign_segments import SegmentCensus, context_from_inputs
from retailops_ai.evaluation_campaign.campaign_source_annotations import SourceAnnotations
from retailops_ai.evaluation_campaign.final_forecast import verify_final_forecast
from retailops_ai.evaluation_campaign.label_contract import EligibilityReason
from retailops_ai.evaluation_campaign.partitions import membership_key, runtime_pin
from retailops_ai.evaluation_campaign.physical_forecast import _index, verify_physical_forecast
from retailops_ai.evaluation_campaign.source_replay import (
    _open_verified_source_parent,
    physical_limits,
)
from retailops_ai.source_snapshot.files import SnapshotError, file_hash
from retailops_ai.source_snapshot.importer import verify_snapshot
from retailops_ai.worker_resources import worker_peak_rss_bytes


def _indexes(root: Path, maximum: int) -> None:
    if sum(p.stat().st_size for p in root.rglob("*.sqlite") if p.is_file()) > maximum:
        raise SnapshotError("campaign_context_bundle_combined_index_budget")


def materialize(
    root: Path,
    request: dict[str, Any],
    recipe: CampaignContextBundleRecipe,
    *,
    source_recipe: CampaignSourceRecipe | None,
) -> dict[str, Any]:
    """Internal; reservation and completion are proved by the public runner.

    The optional source recipe is only for exposed component controls. The CLI
    always requires it, binds the generation plan and checks producer identity.
    """
    generated = CampaignGeneratedParentReceipt.model_validate_json(
        canonical_bytes(request["generated"])
    )
    exported = (
        CampaignFinalExportReceipt if recipe.phase == "final" else CampaignDevelopmentExportReceipt
    ).model_validate_json(canonical_bytes(request["exported"]))
    generation = CampaignGenerationPlan.model_validate_json(canonical_bytes(request["generation"]))
    if source_recipe is not None:
        generation.bind(source_recipe)
    dataset = Path(request["dataset"])
    sealed = _bundle_inventory(dataset, exported.plan.max_artifact_bytes)
    manifest = (verify_final_forecast if recipe.phase == "final" else verify_physical_forecast)(
        dataset
    )
    if (
        manifest.dataset_id != exported.dataset_id
        or manifest.descriptor.recipe != exported.recipe
        or file_hash(dataset, "manifest.json")[1] != exported.manifest_sha256
        or manifest.descriptor.runtime != generated.runtime
        or exported.recipe.source != generated.source
    ):
        raise SnapshotError("campaign_context_bundle_export_manifest_mismatch")
    bundle = root / "bundle"
    bundle.mkdir(mode=0o700)
    write(bundle / "recipe.json", recipe.model_dump(mode="json"))
    write(
        bundle / "parents.json",
        {
            "generated": generated.model_dump(mode="json"),
            "generation": generation.model_dump(mode="json"),
            "exported": exported.model_dump(mode="json"),
        },
    )
    with _open_verified_source_parent(
        Path(request["snapshot"]),
        Path(request["curated"]),
        generated.source,
        limits=physical_limits(generated.source),
        runtime=generated.runtime,
    ) as replay:
        if source_recipe is not None:
            _producer(replay, source_recipe)
        if (
            replay.snapshot_inventory_sha256,
            replay.curated_inventory_sha256,
            replay.logical_curated_sha256,
        ) != (
            exported.snapshot_inventory_sha256,
            exported.curated_inventory_sha256,
            exported.logical_curated_sha256,
        ):
            raise SnapshotError("campaign_context_bundle_export_source_seal_mismatch")
        snapshot = verify_snapshot(
            replay.curated.parent / "snapshot",
            limits=physical_limits(generated.source),
            required_use_cases=generation.required_use_cases,
            scratch=root,
        )
        scope = CampaignForecastContextScope.model_validate_json(
            canonical_bytes(
                {
                    "data_seed": generated.source.source_parameters["seed"],
                    "role": recipe.role,
                    "dataset_id": exported.dataset_id,
                    "source_recipe_sha256": recipe.source_recipe_sha256,
                    "source_dataset_id": snapshot.source_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "curated_dataset_id": replay.manifest["curated_dataset_id"],
                    "source_scenario_plan_sha256": snapshot.manifest["source"]["descriptor"].get(
                        "scenario_plan_sha256"
                    ),
                    "segment_policy_sha256": recipe.segment_policy.content_sha256(),
                }
            )
        )
        annotations = SourceAnnotations(snapshot, generation, scope)
        with (
            closing(_index(root / "inputs.sqlite", recipe.max_index_bytes)) as inputs,
            closing(_index(root / "actuals.sqlite", recipe.max_index_bytes)) as actuals,
            closing(_index(root / "contexts.sqlite", recipe.max_index_bytes)) as contexts,
            CampaignContextFacts(
                replay.curated, scope=scope, policy=recipe.storage_policy, scratch=root
            ) as facts,
        ):
            facts.check_categories(recipe.segment_policy)
            population = data.index_role(inputs, actuals, dataset, manifest, recipe)
            contexts.execute("CREATE TABLE contexts(key BLOB PRIMARY KEY, body BLOB)")
            size = count = 0
            for window in data.windows(inputs, recipe):
                for record in window.records:
                    if any(r not in get_args(EligibilityReason) for r in record.exclusion_reasons):
                        raise SnapshotError("campaign_context_bundle_unknown_eligibility_reason")
                    route = facts.route(record.row)
                    point = facts.point(record.row, route) if route is not None else None
                    context = context_from_inputs(
                        record.row,
                        window.history,
                        point,
                        route,
                        annotations.annotation(record.row),
                        scope=scope,
                        policy=recipe.segment_policy,
                        example_sha256=record.example_sha256,
                        eligible=record.eligible,
                        exclusion_reasons=cast(
                            tuple[EligibilityReason, ...], record.exclusion_reasons
                        ),
                    )
                    raw = canonical_bytes(context.model_dump(mode="json")) + b"\n"
                    size += len(raw)
                    count += 1
                    if len(raw) > recipe.max_record_bytes or size > recipe.max_output_bytes:
                        raise SnapshotError("campaign_context_bundle_context_output_budget")
                    if membership_key(context) != record.key:
                        raise SnapshotError("campaign_context_bundle_context_key_mismatch")
                    contexts.execute("INSERT INTO contexts VALUES(?,?)", (record.key, raw))
                    if count % 256 == 0:
                        contexts.commit()
                        _indexes(root, recipe.max_index_bytes)
            contexts.commit()
            _indexes(root, recipe.max_index_bytes)
            from retailops_ai.evaluation_campaign.campaign_segment_contract import (
                CampaignForecastKeyContext,
            )

            census = SegmentCensus(scope, recipe.segment_policy)
            descriptor = os.open(
                bundle / "contexts.jsonl",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                for key, raw in contexts.execute("SELECT key,body FROM contexts ORDER BY key"):
                    context = CampaignForecastKeyContext.model_validate_json(raw)
                    if membership_key(context) != key:
                        raise SnapshotError("campaign_context_bundle_sorted_key_mismatch")
                    census.add(context)
                    stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            complete = census.finish(
                **{
                    "expected_" + k: population[k]
                    for k in ("rows", "eligible_rows", "keys_sha256", "eligible_keys_sha256")
                }
            )
            facts.check_parent()
            fact_seal, fact_stats = dict(facts.seal), dict(facts.stats)
        # Seals and complete original dataset are checked after all reads, before
        # a successful worker result or public receipt can be produced.
        replay.check_parents()
        if _bundle_inventory(dataset, exported.plan.max_artifact_bytes) != sealed:
            raise SnapshotError("campaign_context_bundle_dataset_changed_during_read")
        write(bundle / "scope.json", scope.model_dump(mode="json"))
        write(bundle / "population.json", population)
        write(bundle / "census.json", complete.model_dump(mode="json"))
        source_seal = {
            "snapshot_inventory_sha256": replay.snapshot_inventory_sha256,
            "curated_inventory_sha256": replay.curated_inventory_sha256,
            "logical_curated_sha256": replay.logical_curated_sha256,
            "source_manifest_sha256": annotations.source_manifest_sha256,
            "source_scenario_plan_sha256": annotations.plan_sha256,
            "facts": fact_seal,
            "fact_stats": fact_stats,
        }
        write(bundle / "source-seal.json", source_seal)
    hashes, artifact_bytes = _bundle_inventory(bundle, recipe.max_output_bytes)
    for name in ("inputs.sqlite", "actuals.sqlite", "contexts.sqlite"):
        (root / name).unlink()
    return {
        "scope": scope.model_dump(mode="json"),
        "population": population,
        "context_trace_sha256": complete.context_trace_sha256,
        "census_sha256": canonical_sha256(complete.model_dump(mode="json")),
        "artifact_files": hashes,
        "artifact_bytes": artifact_bytes,
        "artifact_sha256": canonical_sha256(hashes),
        **source_seal,
        # The complete export verifier parses each role file. index_role then
        # parses the selected role again; neither pass is erased from evidence.
        "full_role_label_file_passes": 2,
        "complete_export_role_file_passes": 1 if recipe.phase == "final" else 6,
    }


def main(root: Path) -> None:
    started, before = perf_counter(), runtime_pin()
    request = read(root / "request.json")
    if before.model_dump(mode="json") != request["runtime"]:
        raise SnapshotError("campaign_context_bundle_worker_runtime_mismatch")
    recipe = CampaignContextBundleRecipe.model_validate_json(canonical_bytes(request["recipe"]))
    result = materialize(
        root,
        request,
        recipe,
        source_recipe=CampaignSourceRecipe.model_validate_json(
            canonical_bytes(request["source_recipe"])
        ),
    )
    if runtime_pin() != before:
        raise SnapshotError("campaign_context_bundle_worker_runtime_changed")
    result.update(
        {
            "worker_peak_rss_bytes": worker_peak_rss_bytes(),
            "worker_wall_seconds": perf_counter() - started,
        }
    )
    write(root / "result.json", result)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
