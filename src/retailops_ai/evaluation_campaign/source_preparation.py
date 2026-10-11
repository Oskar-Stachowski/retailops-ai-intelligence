"""Run the five original native preparation operations with durable stop/resume."""

import json
import logging
import os
import subprocess
from pathlib import Path

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignJournal, CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_generation import validate_completed_generation
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import VARIANTS
from retailops_ai.evaluation_campaign.canonical_development import (
    capture_canonical_development_parents,
    compile_canonical_development_preparation,
)
from retailops_ai.evaluation_campaign.canonical_development_contract import (
    CanonicalDevelopmentPlanningProtocol,
    ResolvedCanonicalDevelopmentPlanningReceipt,
    ResolvedCanonicalDevelopmentPreparationProtocol,
    compile_canonical_development_planning,
)
from retailops_ai.evaluation_campaign.development_execution import _completed_prefix, _receipt
from retailops_ai.evaluation_campaign.development_planning import (
    generate_ordinary_for_planning,
    plan_native_development_scenarios,
    validate_completed_native_planning,
)
from retailops_ai.evaluation_campaign.development_planning_contract import (
    NativeDevelopmentPlanningProtocol,
    ResolvedNativeDevelopmentPlanningReceipt,
    compile_native_development_planning,
)
from retailops_ai.evaluation_campaign.development_policy_freeze import (
    FrozenDevelopmentPolicies,
    verify_catalog_policy_pin,
)
from retailops_ai.evaluation_campaign.development_profiles import DevelopmentProfile
from retailops_ai.evaluation_campaign.development_screening import (
    capture_prepared_development_parents,
)
from retailops_ai.evaluation_campaign.development_variants import (
    compile_resolved_development_preparation,
    generate_resolved_development_variant,
    reuse_ordinary_development_parent,
    validate_reused_development_parent,
)
from retailops_ai.evaluation_campaign.development_variants_contract import (
    ResolvedDevelopmentPreparationProtocol,
    ReusedDevelopmentParentReceipt,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.preparation_execution import locked, write_once
from retailops_ai.evaluation_campaign.source_preparation_contract import (
    SourcePreparationOptions,
    SourcePreparationProducerPin,
    SourcePreparationSession,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    read_bytes,
)

LOGGER = logging.getLogger(__name__)
MAX_BYTES = 4 * 1024**2


def inspect_preparation_producer(
    options: SourcePreparationOptions, policies: FrozenDevelopmentPolicies
) -> SourcePreparationProducerPin:
    producer = Path(options.producer)
    verify_catalog_policy_pin(policies.policies.catalog, producer)
    raw = subprocess.check_output(  # noqa: S603 -- fixed metadata worker, no shell or data generation
        [
            options.producer_python,
            "-I",
            "-B",
            str(Path(__file__).with_name("source_preparation_metadata.py")),
            str(producer),
            policies.policies.catalog.producer_commit,
        ],
        cwd=producer,
        env={k: os.environ[k] for k in ("PATH", "LANG", "SYSTEMROOT") if k in os.environ},
        timeout=30,
    )
    if len(raw) > 16384:
        raise SnapshotError("source_preparation_metadata_limit")
    result = SourcePreparationProducerPin.model_validate_json(raw)
    policies.require_producer(result.producer_commit)
    verify_catalog_policy_pin(policies.policies.catalog, producer)
    return result


def _session(
    root: Path, options: SourcePreparationOptions, policies: FrozenDevelopmentPolicies
) -> SourcePreparationSession:
    if policies.context.runtime != runtime_pin():
        raise SnapshotError("source_preparation_frozen_runtime_changed")
    producer = Path(options.producer).resolve()
    if root != root.resolve() or root.is_relative_to(producer) or producer.is_relative_to(root):
        raise SnapshotError("source_preparation_root_overlaps_producer_or_is_aliased")
    name = "source-preparation.json"
    if (root / name).exists() or (root / name).is_symlink():
        result = SourcePreparationSession.model_validate_json(read_bytes(root, name, MAX_BYTES))
        if result.root != str(root) or result.options != options or result.policies != policies:
            raise SnapshotError("source_preparation_frozen_session_changed")
        return result
    if any(p.name != "execution.lock" for p in root.iterdir()):
        raise SnapshotError("source_preparation_session_must_precede_all_operations")
    result = SourcePreparationSession(
        root=str(root),
        options=options,
        policies=policies,
        producer_pin=inspect_preparation_producer(options, policies),
    )
    write_once(root / name, result.model_dump(mode="json"))
    return result


def _initialize(root: Path, protocol: CampaignProtocol) -> CampaignJournal:
    """A lost/partial journal must never recreate already consumed preparation slots."""
    marker = root.parent / (root.name + "-initialization.json")
    value = {"protocol_sha256": protocol.content_sha256()}
    if marker.exists() or marker.is_symlink():
        if read_bytes(marker.parent, marker.name, MAX_BYTES) != canonical_bytes(value) + b"\n":
            raise SnapshotError("source_preparation_initialization_protocol_changed")
        if not (root / "journal.json").is_file():
            raise SnapshotError("source_preparation_missing_journal_cannot_reset")
    else:
        if root.exists() or root.is_symlink():
            raise SnapshotError("source_preparation_cannot_adopt_unmarked_journal")
        write_once(marker, value)
    return campaign_journal.initialize(root, protocol)


def _bootstrap(root: Path, session: SourcePreparationSession) -> CampaignJournal:
    options = session.options
    values = dict(
        context=session.policies.context,
        **session.producer_pin.model_dump(),
        partitions=options.partitions,
        generation_resources=options.generation_resources,
        planning_resources=options.planning_resources,
        maximum_total_wall_seconds=options.maximum_total_wall_seconds,
    )
    protocol: NativeDevelopmentPlanningProtocol | CanonicalDevelopmentPlanningProtocol
    if options.products == 100:
        protocol = compile_canonical_development_planning(root, **values)
    else:
        protocol = compile_native_development_planning(
            root,
            profile=DevelopmentProfile.model_validate(
                {
                    "name": f"ai09-development-{options.products}-v1",
                    "products": options.products,
                }
            ),
            **values,
        )
    return _initialize(root, protocol)


def _generated_paths(output: Path, receipt: CampaignGeneratedParentReceipt) -> dict[str, str]:
    """Read bounded original worker metadata; raw facts remain behind the next reservation."""
    base = output / receipt.reservation_id
    generated = json.loads(read_bytes(base, "generation.json", MAX_BYTES))
    imported = json.loads(read_bytes(base, "import.json", MAX_BYTES))
    curated = json.loads(read_bytes(base, "curation.json", MAX_BYTES))
    if generated["source_dataset_id"] != receipt.source.parent.source_dataset_id:
        raise SnapshotError("source_preparation_original_generated_identity_changed")
    paths = {
        "raw": generated["directory"],
        "snapshot": str(Path(imported["destination"]) / "snapshot"),
        "curated": curated["destination"],
    }
    for name, value in paths.items():
        if not isinstance(value, str):
            raise SnapshotError("source_preparation_original_output_path_invalid")
        path = Path(value)
        if path != path.resolve() or not path.is_relative_to(base) or path == base:
            raise SnapshotError("source_preparation_original_output_path_escaped")
        checked_directory(path)
        if name == "raw" and not path.is_relative_to(base / "raw"):
            raise SnapshotError("source_preparation_original_raw_path_escaped")
    if len(set(paths.values())) != 3 or (
        file_hash(Path(paths["snapshot"]), "snapshot_manifest.json")[1]
        != receipt.source.snapshot_manifest_sha256
        or file_hash(Path(paths["curated"]), "curated_manifest.json")[1]
        != receipt.source.curated_manifest_sha256
    ):
        raise SnapshotError("source_preparation_original_parent_manifest_changed")
    return paths


def _completed(journal: Path, operation: str, reservation: str) -> dict[str, str]:
    completed = _completed_prefix(campaign_journal.inspect(journal))
    if completed.get(operation) != reservation:
        raise SnapshotError("source_preparation_controller_completion_not_durable")
    LOGGER.info("AI09 preparation completed %s", operation)
    return completed


def _result(new: int, completed: int, *, complete: bool = False) -> dict[str, object]:
    return {
        "new_operations": new,
        "completed_operations": completed,
        "complete": complete,
        "model_fit_authorized": False,
        "final_test_access_authorized": False,
        "stage_ready": False,
    }


def run_source_preparation(
    root: Path,
    options: SourcePreparationOptions,
    policies: FrozenDevelopmentPolicies,
    *,
    maximum_new_operations: int = 5,
) -> dict[str, object]:
    """Continue only a successfully completed prefix; all five original costs survive."""
    if type(maximum_new_operations) is not int or not 1 <= maximum_new_operations <= 5:
        raise SnapshotError("source_preparation_operation_limit")
    options = SourcePreparationOptions.model_validate_json(options.model_dump_json())
    policies = FrozenDevelopmentPolicies.model_validate_json(policies.model_dump_json())
    root = root.absolute()
    with locked(root):
        session = _session(root, options, policies)
        bootstrap, variants, output = root / "planning", root / "variants", root / "artifacts"
        ledger = _bootstrap(bootstrap, session)
        completed = _completed_prefix(ledger)
        output.mkdir(mode=0o700, exist_ok=True)
        checked_directory(output)
        if output.stat().st_mode & 0o777 != 0o700:
            raise SnapshotError("source_preparation_private_artifacts_required")
        new = 0
        generation_key, planning_key = (o.operation_id for o in ledger.protocol.operations)
        LOGGER.info(
            "AI09 preparation %s %s",
            "verify_original" if generation_key in completed else "start",
            generation_key,
        )
        if generation_key in completed:
            generated = _receipt(
                bootstrap, completed, generation_key, CampaignGeneratedParentReceipt
            )
        else:
            _, _, generated = generate_ordinary_for_planning(
                journal=bootstrap,
                producer=Path(options.producer),
                producer_python=Path(options.producer_python),
                output_root=output,
            )
            new += 1
        validate_completed_generation(bootstrap, generated)
        completed = _completed(bootstrap, generation_key, generated.reservation_id)
        parents_paths = {"ordinary": _generated_paths(output, generated)}
        if planning_key not in completed and new == maximum_new_operations:
            return _result(new, len(completed))
        LOGGER.info(
            "AI09 preparation %s %s",
            "verify_original" if planning_key in completed else "start",
            planning_key,
        )
        planned: (
            ResolvedCanonicalDevelopmentPlanningReceipt | ResolvedNativeDevelopmentPlanningReceipt
        )
        if planning_key in completed:
            if options.products == 100:
                planned = _receipt(
                    bootstrap, completed, planning_key, ResolvedCanonicalDevelopmentPlanningReceipt
                )
            else:
                planned = _receipt(
                    bootstrap, completed, planning_key, ResolvedNativeDevelopmentPlanningReceipt
                )
        else:
            _, planned = plan_native_development_scenarios(
                journal=bootstrap,
                generated=generated,
                raw_source=Path(parents_paths["ordinary"]["raw"]),
                producer=Path(options.producer),
                producer_python=Path(options.producer_python),
                output_root=output,
            )
            new += 1
        validate_completed_native_planning(bootstrap, planned)
        completed = _completed(bootstrap, planning_key, planned.reservation_id)
        if file_hash(output / planned.reservation_id, "native-plans.json") != (
            planned.bundle_bytes,
            planned.bundle_sha256,
        ):
            raise SnapshotError("source_preparation_original_native_plans_changed")
        protocol: (
            ResolvedCanonicalDevelopmentPreparationProtocol | ResolvedDevelopmentPreparationProtocol
        )
        if isinstance(planned, ResolvedCanonicalDevelopmentPlanningReceipt):
            protocol = compile_canonical_development_preparation(
                variants,
                bootstrap_journal=bootstrap,
                generated=generated,
                planned=planned,
                reuse_resources=options.reuse_resources,
                maximum_total_wall_seconds=options.maximum_total_wall_seconds,
            )
        else:
            protocol = compile_resolved_development_preparation(
                variants,
                bootstrap_journal=bootstrap,
                generated=generated,
                planned=planned,
                reuse_resources=options.reuse_resources,
                maximum_total_wall_seconds=options.maximum_total_wall_seconds,
            )
        variant_ledger = _initialize(variants, protocol)
        done = _completed_prefix(variant_ledger)
        variant_receipts: dict[str, CampaignGeneratedParentReceipt] = {}
        ordinary: ReusedDevelopmentParentReceipt | None = None
        for variant, operation in zip(VARIANTS, protocol.operations, strict=True):
            key = operation.operation_id
            if key not in done and new == maximum_new_operations:
                return _result(new, len(completed) + len(done))
            LOGGER.info(
                "AI09 preparation %s %s", "verify_original" if key in done else "start", key
            )
            reused = key in done
            if variant == "ordinary":
                ordinary = (
                    _receipt(variants, done, key, ReusedDevelopmentParentReceipt)
                    if reused
                    else reuse_ordinary_development_parent(
                        journal=variants,
                        snapshot=Path(parents_paths["ordinary"]["snapshot"]),
                        curated=Path(parents_paths["ordinary"]["curated"]),
                        output_root=output,
                    )
                )
                validate_reused_development_parent(variants, ordinary)
                reservation = ordinary.reservation_id
            else:
                if reused:
                    receipt = _receipt(variants, done, key, CampaignGeneratedParentReceipt)
                else:
                    _, _, receipt = generate_resolved_development_variant(
                        journal=variants,
                        variant=variant,
                        producer=Path(options.producer),
                        producer_python=Path(options.producer_python),
                        output_root=output,
                    )
                validate_completed_generation(variants, receipt)
                variant_receipts[variant] = receipt
                parents_paths[variant] = _generated_paths(output, receipt)
                reservation = receipt.reservation_id
            done = _completed(variants, key, reservation)
            new += int(not reused)
        if ordinary is None:
            raise SnapshotError("source_preparation_original_reuse_missing")
        capture = (
            capture_canonical_development_parents
            if options.products == 100
            else capture_prepared_development_parents
        )
        parents = capture(
            variants,
            ordinary=ordinary,
            demand=variant_receipts["demand"],
            physical=variant_receipts["physical"],
        )
        value = {
            "version": "ai09-original-source-preparation-result-1.0.0",
            "session_sha256": canonical_sha256(session.model_dump(mode="json")),
            "parents": parents.model_dump(mode="json"),
            "parent_paths": parents_paths,
            "model_fit_authorized": False,
            "final_test_access_authorized": False,
            "stage_ready": False,
        }
        name = "source-preparation-result.json"
        if (root / name).exists() or (root / name).is_symlink():
            if read_bytes(root, name, MAX_BYTES) != canonical_bytes(value) + b"\n":
                raise SnapshotError("source_preparation_original_result_changed")
        else:
            write_once(root / name, value)
        return _result(new, 5, complete=True)
