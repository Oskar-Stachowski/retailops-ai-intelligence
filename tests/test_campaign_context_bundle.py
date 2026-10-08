"""Exposed native fixture roles and audit failures; no Project/fresh final access."""

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest
from pydantic import ValidationError
from test_ai09_campaign_journal import development, protocol_document, selection
from test_campaign_final_export import native_spec
from test_physical_forecast import source as declared_source

from retailops_ai.curated.builder import build_curated, iter_rows
from retailops_ai.data_contracts.common import DateWindow, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_context_bundle as api
from retailops_ai.evaluation_campaign import campaign_context_bundle_worker as worker
from retailops_ai.evaluation_campaign import campaign_evaluation_data as data
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import final_forecast as final_data
from retailops_ai.evaluation_campaign.campaign_context_bundle_contract import (
    FILES,
    CampaignContextBundleReceipt,
    CampaignContextBundleRecipe,
)
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost, CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_export import _store_receipt
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportPlan,
    CampaignDevelopmentExportReceipt,
    CampaignGeneratedParentReceipt,
)
from retailops_ai.evaluation_campaign.campaign_final_contract import (
    CampaignFinalExportPlan,
    CampaignFinalExportReceipt,
)
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationPlan,
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignForecastKeyContext,
    CampaignForecastSegmentPolicy,
)
from retailops_ai.evaluation_campaign.final_forecast import (
    _build_final_forecast,
    verify_final_forecast,
)
from retailops_ai.evaluation_campaign.partitions import (
    chronological_policy,
    membership_key,
    runtime_pin,
)
from retailops_ai.evaluation_campaign.source_replay import (
    _open_verified_source_parent,
    physical_limits,
)
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.source_snapshot.files import SnapshotError, file_hash
from retailops_ai.source_snapshot.importer import import_snapshot


def resources():
    return CampaignGenerationResources(
        wall_seconds=120,
        tree_rss_bytes=512 * 1024**2,
        scratch_bytes=512 * 1024**2,
        minimum_free_disk_bytes=1024,
        minimum_available_memory_bytes=1024,
    )


@pytest.fixture(scope="module", params=["demand", "physical"])
def native_case(request, tmp_path_factory):
    root = tmp_path_factory.mktemp("ai09-context-role-control").resolve()
    prefix = request.param + "/public"
    with ZipFile(Path(__file__).parents[1] / "data/fixtures/anomaly-v1_2.zip") as archive:
        for item in archive.infolist():
            if item.filename.startswith(prefix + "/"):
                archive.extract(item, root / "exposed")
        # Previously exposed fixture metadata, not Project preregistration proof.
        plan = json.loads(
            archive.read(request.param + "/private/evaluation_truth/anomaly_scenario.json")
        )["plan"]
    imported = import_snapshot(root / "exposed" / prefix, root / "input/data/generated")
    snapshot = imported.directory / "snapshot"
    curated = build_curated(imported.directory, root / "curated/data/generated")
    spec = native_spec(snapshot, curated.directory)
    source = json.loads((snapshot / "snapshot_manifest.json").read_bytes())["source"]
    catalog = next(t for t in curated.manifest["tables"] if t["table"] == "product_catalog")
    policy = CampaignForecastSegmentPolicy(
        category_inventory=tuple(
            sorted({r["category_id"] for r in iter_rows(curated.directory, catalog["files"], 256)})
        )
    )
    digest = canonical_sha256("exposed-component-control-only")
    generation = CampaignGenerationPlan(
        source_recipe_sha256=digest,
        exporter_lock_sha256="a" * 64,
        requested_parameters=source["requested_parameters"],
        resolved_parameters=spec.source_parameters,
        entrypoint="planned_anomaly",
        scenario_plan=plan,
        snapshot_schema_version="1.2.0",
        required_use_cases=("forecast_source", "inventory_source", "anomaly_source"),
        resources=resources(),
    )
    export_plan = CampaignFinalExportPlan(
        source_recipe_sha256=digest,
        generation_operation_id="controlled-generation",
        origins=OriginWindow(start=date(2026, 7, 3), end=date(2026, 7, 4)),
        label_knowledge_cutoff=end_of_day(date(2026, 7, 31)),
        prior_exposure_end=date(2026, 6, 17),
    )
    runtime = runtime_pin()
    generated = CampaignGeneratedParentReceipt(
        protocol_sha256="b" * 64,
        source_recipe_sha256=digest,
        operation_id=export_plan.generation_operation_id,
        reservation_id="campaign-operation-" + "1" * 32,
        source=spec,
        runtime=runtime,
    )
    with _open_verified_source_parent(
        snapshot, curated.directory, spec, limits=physical_limits(spec), runtime=runtime
    ) as replay:
        dataset = _build_final_forecast(replay, export_plan.bind(spec), root / "forecast")
    manifest = verify_final_forecast(dataset)
    exported = CampaignFinalExportReceipt(
        protocol_sha256=generated.protocol_sha256,
        operation_id="controlled-export",
        reservation_id="campaign-operation-" + "2" * 32,
        plan=export_plan,
        generated_parent_receipt_sha256=generated.content_sha256(),
        selection_sha256="c" * 64,
        recipe=manifest.descriptor.recipe,
        dataset_id=manifest.dataset_id,
        manifest_sha256=file_hash(dataset, "manifest.json")[1],
        runtime_code_sha256=runtime.code_sha256,
        snapshot_inventory_sha256=manifest.descriptor.snapshot_inventory_sha256,
        curated_inventory_sha256=manifest.descriptor.curated_inventory_sha256,
        logical_curated_sha256=manifest.descriptor.logical_curated_sha256,
        population_rows=manifest.descriptor.population.row_count,
        artifact_bytes=_bundle_inventory(dataset, export_plan.max_artifact_bytes)[1],
    )
    recipe = CampaignContextBundleRecipe(
        phase="final",
        role="final_test",
        source_recipe_sha256=digest,
        generation_operation_id=generated.operation_id,
        export_operation_id=exported.operation_id,
        segment_policy=policy,
        resources=resources(),
    )
    request = {
        "snapshot": str(snapshot),
        "curated": str(curated.directory),
        "dataset": str(dataset),
        "generated": generated.model_dump(mode="json"),
        "generation": generation.model_dump(mode="json"),
        "exported": exported.model_dump(mode="json"),
    }
    scratch = root / "materialization"
    scratch.mkdir(mode=0o700)
    parsed = []
    with pytest.MonkeyPatch.context() as patch:
        for module in (data, final_data):
            original = module.regular_file

            def tracked(parent, name, *, original=original):
                if parent == dataset and name == "final_evaluation.jsonl":
                    parsed.append(name)
                return original(parent, name)

            patch.setattr(module, "regular_file", tracked)
        result = worker.materialize(scratch, request, recipe, source_recipe=None)
    assert len(parsed) == result["full_role_label_file_passes"] == 2
    receipt = CampaignContextBundleReceipt(
        protocol_sha256=generated.protocol_sha256,
        operation_id="controlled-context",
        reservation_id="campaign-operation-" + "3" * 32,
        recipe=recipe,
        generated_parent_receipt_sha256=generated.content_sha256(),
        generation_plan_sha256=generation.content_sha256(),
        export_receipt_sha256=exported.content_sha256(),
        runtime_code_sha256=runtime.code_sha256,
        scope=result["scope"],
        **{
            k: result["population"][k]
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
            )
        },
        selection_sha256=exported.selection_sha256,
        complete_export_role_file_passes=result["complete_export_role_file_passes"],
        worker_evidence={"component_control_only": True},
    )
    return scratch, request, recipe, receipt


def test_native_whole_role_sorted_context_all_keys_no_actuals(native_case):
    root, request, recipe, receipt = native_case
    api._verify_contents(root / "bundle", receipt)
    rows = [
        CampaignForecastKeyContext.model_validate_json(raw)
        for raw in (root / "bundle/contexts.jsonl").read_bytes().splitlines()
    ]
    keys = [membership_key(r) for r in rows]
    assert keys == sorted(set(keys)) and len(keys) == receipt.rows > 0
    assert hashlib.sha256(b"".join(k + b"\n" for k in keys)).hexdigest() == receipt.keys_sha256
    assert {r.horizon_days for r in rows} == set(range(1, 15))
    assert all(r.context_scope_sha256 == receipt.scope.content_sha256() for r in rows)
    assert all(
        r.annotation.source_scenario_plan_sha256 == receipt.scope.source_scenario_plan_sha256
        for r in rows
    )
    assert all(r.role == "final_test" and not r.final_access_authorized for r in rows)
    assert all("actual" not in r.model_dump() and "effects" not in r.model_dump() for r in rows)
    assert (
        not list(root.rglob("*.sqlite"))
        and not receipt.stage_ready
        and not receipt.quality_qualified
    )
    assert set(receipt.artifact_files) == FILES
    assert receipt.full_role_label_file_passes == 2
    assert receipt.complete_export_role_file_passes == 1


@pytest.mark.parametrize(
    "attack", ["drop", "duplicate", "reverse", "context", "census", "parent", "scope", "seal"]
)
def test_resealed_bundle_still_requires_complete_source_role_join(native_case, tmp_path, attack):
    root, _, _, receipt = native_case
    target = tmp_path / "bundle"
    shutil.copytree(root / "bundle", target)
    rows = (target / "contexts.jsonl").read_bytes().splitlines(keepends=True)
    if attack in ("drop", "duplicate", "reverse", "context"):
        if attack == "drop":
            rows.pop()
        elif attack == "duplicate":
            rows.append(rows[-1])
        elif attack == "reverse":
            rows.reverse()
        else:
            row = json.loads(rows[0])
            row["example_sha256"] = "0" * 64
            rows[0] = canonical_bytes(row) + b"\n"
        (target / "contexts.jsonl").write_bytes(b"".join(rows))
    else:
        name = {
            "census": "census.json",
            "parent": "parents.json",
            "scope": "scope.json",
            "seal": "source-seal.json",
        }[attack]
        doc = json.loads((target / name).read_bytes())
        if attack == "census":
            doc["rows"] += 1
        elif attack == "parent":
            doc["generation"]["exporter_lock_sha256"] = "0" * 64
        elif attack == "scope":
            doc["snapshot_id"] = "snapshot-sha256-" + "0" * 64
        else:
            doc["snapshot_inventory_sha256"] = "0" * 64
        (target / name).write_bytes(canonical_bytes(doc) + b"\n")
    hashes, size = _bundle_inventory(target, receipt.recipe.max_output_bytes)
    resealed = receipt.model_copy(
        update={
            "artifact_files": hashes,
            "artifact_bytes": size,
            "artifact_sha256": canonical_sha256(hashes),
        }
    )
    with pytest.raises((SnapshotError, ValidationError)):
        api._verify_contents(target, resealed)


def controlled_campaign(tmp_path, *, final=False):
    root = tmp_path.resolve() / "journal"
    doc = protocol_document(root)
    source = doc["sources"][1 if final else 0]
    old = canonical_sha256(source)
    parameters = {
        "profile": "ai-training" if final else "ai-dev",
        "seed": 42,
        "days": 730 if final else 365,
        "products": 200 if final else 100,
        "stores": 10 if final else 5,
        "warehouses": 4 if final else 3,
        "start_date": source["history"]["start"],
        "end_date": source["history"]["end"],
        "business_timezone": "UTC",
    }
    source.update(
        generation_config_sha256=canonical_sha256(parameters), exporter_lock_sha256="a" * 64
    )
    digest = canonical_sha256(source)
    generation = CampaignGenerationPlan(
        source_recipe_sha256=digest,
        exporter_lock_sha256="a" * 64,
        requested_parameters=parameters,
        resolved_parameters=parameters,
        entrypoint="cached_inventory_v2",
        snapshot_schema_version="1.1.0",
        resources=resources(),
    )
    temporal = chronological_policy(DateWindow(start=date(2026, 3, 9), end=date(2026, 7, 16)))
    export_plan = CampaignDevelopmentExportPlan(
        source_recipe_sha256=digest,
        generation_operation_id="development-42-generate",
        roles=temporal.roles,
        origins=OriginWindow(
            start=temporal.roles[0].origins.start, end=temporal.roles[-1].origins.end
        ),
    )
    if final:
        export_plan = CampaignFinalExportPlan(
            source_recipe_sha256=digest,
            generation_operation_id="final-42-generate",
            origins=OriginWindow.model_validate_json(canonical_bytes(source["evaluation_origins"])),
            label_knowledge_cutoff=end_of_day(date.fromisoformat(source["history"]["end"])),
            prior_exposure_end=date(2026, 7, 31),
        )
    recipe = CampaignContextBundleRecipe(
        phase="final" if final else "development",
        role="final_test" if final else "development_evaluation",
        source_recipe_sha256=digest,
        generation_operation_id=export_plan.generation_operation_id,
        export_operation_id="final-42-read" if final else "development-42-read",
        segment_policy=CampaignForecastSegmentPolicy(category_inventory=("controlled-category",)),
        resources=resources(),
    )
    for op in doc["operations"]:
        if op["source_recipe_sha256"] == old:
            op["source_recipe_sha256"] = digest
        if op["operation_id"] == recipe.generation_operation_id:
            op["execution_recipe_sha256"] = generation.content_sha256()
        if op["operation_id"] == recipe.export_operation_id:
            op["execution_recipe_sha256"] = export_plan.content_sha256()
    doc["operations"].insert(
        next(
            i + 1
            for i, op in enumerate(doc["operations"])
            if op["operation_id"] == recipe.export_operation_id
        ),
        {
            "operation_id": "final-context" if final else "development-context",
            "phase": recipe.phase,
            "source_recipe_sha256": digest,
            "execution_recipe_sha256": recipe.content_sha256(),
            "action": "source_read",
            "use_case": "source",
            "role": "all_parent_data",
            "prerequisites": [recipe.generation_operation_id, recipe.export_operation_id],
        },
    )
    doc["maximum_new_attempts"] += 1
    doc["segment_policy_sha256"] = recipe.segment_policy.content_sha256()
    protocol = CampaignProtocol.model_validate_json(canonical_bytes(doc))
    journal.initialize(root, protocol)
    if final:
        development(root)
        journal.freeze_selection(root, selection(root))
    spec = declared_source().model_copy(
        update={"schema_version": "1.1.0", "source_parameters": parameters}
    )
    reserved = journal.reserve(root, recipe.generation_operation_id)
    generated = CampaignGeneratedParentReceipt(
        protocol_sha256=protocol.content_sha256(),
        source_recipe_sha256=digest,
        operation_id=recipe.generation_operation_id,
        reservation_id=str(reserved.reservation_id),
        source=spec,
        runtime=protocol.runtime,
    )
    _store_receipt(root, generated)
    journal.finish(
        root,
        str(reserved.reservation_id),
        result="completed",
        evidence_sha256=generated.content_sha256(),
        cost=CampaignCost(wall_seconds=0.01),
    )
    reserved = journal.reserve(root, recipe.export_operation_id)
    fields = dict(
        protocol_sha256=protocol.content_sha256(),
        operation_id=recipe.export_operation_id,
        reservation_id=str(reserved.reservation_id),
        plan=export_plan,
        generated_parent_receipt_sha256=generated.content_sha256(),
        recipe=export_plan.bind(spec),
        dataset_id=("ai09-final-forecast-sha256-" if final else "ai09-physical-forecast-sha256-")
        + "1" * 64,
        manifest_sha256="2" * 64,
        runtime_code_sha256=protocol.runtime.code_sha256,
        snapshot_inventory_sha256="3" * 64,
        curated_inventory_sha256="4" * 64,
        logical_curated_sha256="5" * 64,
        population_rows=5,
        artifact_bytes=128,
    )
    if final:
        event = next(e for e in journal.inspect(root).events if e.kind == "selection_frozen")
        exported = CampaignFinalExportReceipt(
            **fields, selection_sha256=canonical_sha256(event.selection.model_dump(mode="json"))
        )
    else:
        exported = CampaignDevelopmentExportReceipt(**fields)
    _store_receipt(root, exported)
    journal.finish(
        root,
        str(reserved.reservation_id),
        result="completed",
        evidence_sha256=exported.content_sha256(),
        cost=CampaignCost(wall_seconds=0.01, artifact_bytes=128),
    )
    return root, recipe, generation, generated, exported


def run(case, tmp_path, **changes):
    root, recipe, generation, generated, exported = case
    output = tmp_path / "output"
    output.mkdir(mode=0o700, exist_ok=True)
    return api.build_campaign_context_bundle(
        tmp_path / "unread-snapshot",
        tmp_path / "unread-curated",
        tmp_path / "unread-dataset",
        output,
        journal=root,
        operation_id=changes.pop("operation_id", "development-context"),
        recipe=changes.pop("recipe", recipe),
        generation=changes.pop("generation", generation),
        generated=changes.pop("generated", generated),
        exported=changes.pop("exported", exported),
        **changes,
    )


@pytest.mark.parametrize("attack", ["recipe", "generation", "export", "stored_generation"])
def test_wrong_parent_or_recipe_fails_before_worker_and_charge_is_retained(
    tmp_path, monkeypatch, attack
):
    case = controlled_campaign(tmp_path)
    root, recipe, generation, generated, exported = case

    def no_worker(*args, **kwargs):
        raise AssertionError("parent mismatch must precede worker or parent I/O")

    monkeypatch.setattr(api, "monitor", no_worker)
    changes = {}
    if attack == "recipe":
        changes["recipe"] = recipe.model_copy(update={"max_rows": 100})
    elif attack == "generation":
        changes["generation"] = generation.model_copy(update={"exporter_lock_sha256": "0" * 64})
    elif attack == "export":
        changes["exported"] = exported.model_copy(update={"manifest_sha256": "0" * 64})
    else:
        (root / "receipts" / (generated.reservation_id + ".json")).write_bytes(b"tampered\n")
    with pytest.raises(SnapshotError):
        run(case, tmp_path, **changes)
    event = journal.inspect(root).events[-1]
    assert (
        event.result == "failed"
        and event.operation_id == "development-context"
        and event.cost.wall_seconds > 0
    )
    assert not (root / "receipts" / (str(event.reservation_id) + ".json")).exists()


def test_wrong_operation_consumes_no_unrelated_slot(tmp_path):
    case = controlled_campaign(tmp_path)
    before = len(journal.inspect(case[0]).events)
    with pytest.raises(SnapshotError, match="whole_parent_read"):
        run(case, tmp_path, operation_id="development-fit")
    assert len(journal.inspect(case[0]).events) == before


def test_real_worker_failure_is_charged_without_receipt_or_retry(tmp_path):
    case = controlled_campaign(tmp_path)
    with pytest.raises(SnapshotError, match="worker_failed"):
        run(case, tmp_path)
    event = journal.inspect(case[0]).events[-1]
    assert event.result == "failed" and event.cost.wall_seconds > 0
    assert event.cost.peak_process_tree_rss_bytes > 0
    with pytest.raises((SnapshotError, ValidationError), match="budget_exhausted"):
        run(case, tmp_path)


@pytest.mark.parametrize(
    "field",
    [
        "stage_ready",
        "promotion_allowed",
        "final_access_authorized_by_this_document",
        "private_source_effects_read",
    ],
)
def test_metadata_cannot_grant_scientific_or_final_access(tmp_path, field):
    case = controlled_campaign(tmp_path)
    value = case[1].model_dump(mode="json")
    value[field] = True
    with pytest.raises(ValidationError):
        CampaignContextBundleRecipe.model_validate_json(canonical_bytes(value))


def test_generic_role_reader_rejects_unreviewed_plan_type():
    with pytest.raises(SnapshotError, match="unknown_role_index_plan"):
        data._plan(object())


def test_native_role_budget_failure_cannot_return_smaller_context(native_case, tmp_path):
    _, request, recipe, _ = native_case
    with pytest.raises(SnapshotError, match="complete_population_limit"):
        worker.materialize(
            tmp_path, request, recipe.model_copy(update={"max_rows": 1}), source_recipe=None
        )
    assert not (tmp_path / "result.json").exists()


def test_standalone_receipt_cannot_authorize_unrecorded_native_context(native_case, tmp_path):
    root, _, _, receipt = native_case
    with pytest.raises(SnapshotError):
        api.verify_campaign_context_bundle(
            root / "bundle", journal=tmp_path / "absent-journal", receipt=receipt
        )


@pytest.mark.parametrize("parent", ["snapshot", "curated", "dataset"])
def test_output_cannot_mutate_or_contain_a_source_parent(tmp_path, monkeypatch, parent):
    case = controlled_campaign(tmp_path)
    root, recipe, generation, generated, exported = case
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    paths = {name: tmp_path / ("unread-" + name) for name in ("snapshot", "curated", "dataset")}
    paths[parent] = output / "parent"

    def no_worker(*args, **kwargs):
        raise AssertionError("output/parent overlap must precede I/O")

    monkeypatch.setattr(api, "monitor", no_worker)
    with pytest.raises(SnapshotError, match="distinct_output_and_parents"):
        api.build_campaign_context_bundle(
            paths["snapshot"],
            paths["curated"],
            paths["dataset"],
            output,
            journal=root,
            operation_id="development-context",
            recipe=recipe,
            generation=generation,
            generated=generated,
            exported=exported,
        )
    assert not list(output.iterdir())
    assert journal.inspect(root).events[-1].result == "failed"


def test_final_header_freeze_without_three_actual_use_evaluations_cannot_start_worker(
    tmp_path, monkeypatch
):
    case = controlled_campaign(tmp_path, final=True)

    def no_worker(*args, **kwargs):
        raise AssertionError("actual all-three-use selection proof must precede parent I/O")

    monkeypatch.setattr(api, "monitor", no_worker)
    with pytest.raises(SnapshotError, match="completed_use_evaluation"):
        run(
            case,
            tmp_path,
            operation_id="final-context",
            selection_bundles={use: tmp_path / use for use in ("forecast", "anomaly", "stockout")},
        )
    event = journal.inspect(case[0]).events[-1]
    assert event.result == "failed" and event.operation_id == "final-context"
