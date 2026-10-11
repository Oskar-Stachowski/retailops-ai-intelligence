"""Durable two-parent read controls with explicit mocked witnesses, never Project execution."""

import sys
from datetime import date
from pathlib import Path

import pytest
from test_ai09_campaign_journal import complete, selection
from test_anomaly_detectors import scope
from test_campaign_portfolio import KINDS, document
from test_physical_forecast import source as declared_source

from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.anomaly_evaluation.paired_source_comparison import POLICY, SOURCE_TABLES
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import campaign_paired_truth as runner
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_export import _store_receipt
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationPlan,
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_generation_worker import write
from retailops_ai.evaluation_campaign.campaign_paired_truth_contract import (
    CampaignPairedTruthPlan,
    PairedSourceParent,
    PairedSourceVerification,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import CampaignPortfolioProtocol
from retailops_ai.source_snapshot.files import SnapshotError


def case(tmp_path, *, final=False, ordinary_complete=True):
    root = tmp_path.resolve() / "journal"
    raw = document(root)
    remap, parameters = {}, {}
    for source in raw["sources"]:
        previous = canonical_sha256(source)
        params = {
            "profile": source["profile"],
            "seed": source["seed"],
            "days": 365 if source["phase"] == "development" else 730,
            "products": source["products"],
            "stores": source["selling_pairs"],
            "warehouses": source["stock_locations"],
            "business_timezone": "UTC",
            "start_date": source["history"]["start"],
            "end_date": source["history"]["end"],
            "forecast_plan_days": 14,
        }
        source["generation_config_sha256"] = canonical_sha256(params)
        digest = canonical_sha256(source)
        remap[previous], parameters[digest] = digest, params
    for operation in raw["operations"]:
        operation["source_recipe_sha256"] = remap[operation["source_recipe_sha256"]]
    raw["training_source_recipe_sha256"] = {
        k: remap[v] for k, v in raw["training_source_recipe_sha256"].items()
    }
    phase = "final" if final else "development"
    offset = 3 if final else 0
    sources = raw["sources"][offset : offset + 2]
    resources = CampaignGenerationResources(
        wall_seconds=60,
        tree_rss_bytes=256 * 1024**2,
        scratch_bytes=64 * 1024**2,
        minimum_available_memory_bytes=1024**3,
        minimum_free_disk_bytes=6 * 1024**3,
    )
    plans = []
    for source in sources:
        ordinary = source["variant"] == "ordinary"
        digest = canonical_sha256(source)
        plan = CampaignGenerationPlan(
            source_recipe_sha256=digest,
            exporter_lock_sha256=source["exporter_lock_sha256"],
            requested_parameters=parameters[digest],
            resolved_parameters=parameters[digest],
            entrypoint="cached_inventory_v2" if ordinary else "planned_anomaly",
            scenario_plan=None
            if ordinary
            else {"contract_version": KINDS["demand"], "fixture": True},
            snapshot_schema_version="1.1.0" if ordinary else "1.2.0",
            required_use_cases=("forecast_source", "inventory_source")
            + (() if ordinary else ("anomaly_source",)),
            resources=resources,
        )
        plans.append(plan)
        identifier = f"{phase}-42-{source['variant']}-generate"
        next(o for o in raw["operations"] if o["operation_id"] == identifier)[
            "execution_recipe_sha256"
        ] = plan.content_sha256()
    identifier = f"{phase}-42-demand-read"
    plan = CampaignPairedTruthPlan(
        phase=phase,
        source_recipe_sha256=plans[1].source_recipe_sha256,
        ordinary_source_recipe_sha256=plans[0].source_recipe_sha256,
        generation_operation_id=f"{phase}-42-demand-generate",
        ordinary_generation_operation_id=f"{phase}-42-ordinary-generate",
        scenario_plan_sha256=canonical_sha256(plans[1].scenario_plan),
        window=Window(start=date(2026, 9 if final else 7, 1), end=date(2026, 9 if final else 7, 3)),
        resources=resources,
    )
    next(o for o in raw["operations"] if o["operation_id"] == identifier)[
        "execution_recipe_sha256"
    ] = plan.content_sha256()
    protocol = CampaignPortfolioProtocol.model_validate_json(canonical_bytes(raw))
    journal.initialize(root, protocol)
    if final:
        for operation in protocol.operations:
            if operation.phase == "development":
                complete(root, operation.operation_id)
        journal.freeze_selection(root, selection(root))
    parents = []
    for index, generation in enumerate(plans):
        operation_id = (
            plan.ordinary_generation_operation_id if index == 0 else plan.generation_operation_id
        )
        reservation_id = str(journal.reserve(root, operation_id).reservation_id)
        spec = declared_source().model_dump(mode="json")
        spec["source_parameters"] = generation.resolved_parameters
        spec["schema_version"] = generation.snapshot_schema_version
        spec["parent"]["source_dataset_id"] = "source-sha256-" + ("1" if index == 0 else "b") * 64
        parent = CampaignGeneratedParentReceipt.model_validate_json(
            canonical_bytes(
                {
                    "protocol_sha256": protocol.content_sha256(),
                    "source_recipe_sha256": generation.source_recipe_sha256,
                    "operation_id": operation_id,
                    "reservation_id": reservation_id,
                    "source": spec,
                    "runtime": protocol.runtime.model_dump(mode="json"),
                }
            )
        )
        parents.append(parent)
        if index or ordinary_complete:
            _store_receipt(root, parent)
            journal.finish(
                root,
                reservation_id,
                result="completed",
                evidence_sha256=parent.content_sha256(),
                cost=CampaignCost(wall_seconds=0.01),
            )
    output = tmp_path / "output"
    output.mkdir()
    return root, output, identifier, plan, parents, plans


def execute(value, **changes):
    root, output, operation, plan, parents, generations = value
    return runner.read_campaign_paired_truth(
        output,
        output,
        output,
        Path(sys.executable),
        output,
        journal=root,
        operation_id=operation,
        plan=changes.get("plan", plan),
        ordinary=changes.get("ordinary", parents[0]),
        generated=parents[1],
        ordinary_plan=generations[0],
        generation_plan=generations[1],
    )


def controlled(monkeypatch, value, *, failure=None):
    root, _, operation, plan, parents, _ = value
    calls = []
    monkeypatch.setattr(runner, "_producer_pin", lambda *_: None)

    def run(*args, **kwargs):
        assert journal.inspect(root).events[-1].kind == "reserved"
        assert journal.inspect(root).events[-1].operation_id == operation
        calls.append(operation)
        if failure == "worker":
            return {"status": "failed", "sampled_tree_peak_rss_bytes": 12345}
        target = kwargs["root"]
        request = runner.read(target / "request.json")
        windows = [
            {
                **scope().model_dump(mode="json"),
                "window": plan.window.model_dump(mode="json"),
                "available_at": f"2026-{'09' if plan.phase == 'final' else '07'}-05T00:00:00Z",
            }
        ]
        counts = {name: int(name == "products") for name in sorted(SOURCE_TABLES)}
        comparison = {
            "policy": POLICY,
            "parent_table_rows": {"ordinary": counts, "planned": counts},
            "unknown_from_by_product": {},
        }
        witnesses = [
            PairedSourceParent(
                source_dataset_id=p.source.parent.source_dataset_id,
                source_schema_version="2.7.0" if i == 0 else "2.8.0",
                source_descriptor_sha256=p.source.parent.source_dataset_id.removeprefix(
                    "source-sha256-"
                ),
                source_manifest_sha256="1" * 64,
                source_report_sha256="2" * 64,
                source_table_inventory_sha256="3" * 64,
                source_rows=1,
            )
            for i, p in enumerate(parents)
        ]
        verified = PairedSourceVerification(
            ordinary=witnesses[0],
            planned=witnesses[1],
            producer_commit=request["producer_commit"],
            producer_code_sha256="4" * 64,
            producer_lock_sha256=request["producer_lock_sha256"],
            exporter_lock_sha256=request["exporter_lock_sha256"],
            producer_python_version="3.11.15",
            resolved_parameters=request["resolved_parameters"],
            inventory_configuration_sha256="5" * 64,
            source_context_sha256="6" * 64,
            source_scenario_sha256="7" * 64,
            scenario_plan_sha256=plan.scenario_plan_sha256,
            window=plan.window,
            comparison_sha256=canonical_sha256(comparison),
            comparison_policy_sha256=canonical_sha256(POLICY),
            clean_windows_sha256=canonical_sha256(windows),
            clean_window_count=1,
            complete_windows_sha256=canonical_sha256(windows),
            complete_window_count=1,
            complete_observation_count=3,
            episodes_sha256=canonical_sha256([]),
            episode_count=0,
        )
        write(target / "paired-source-verification.json", verified.model_dump(mode="json"))
        write(target / "paired-source-comparison.json", comparison)
        write(target / "paired-source-labels.json", {"complete_windows": windows, "episodes": []})
        write(
            target / "truth-worker-resources.json",
            {
                "worker_peak_rss_bytes": plan.resources.tree_rss_bytes + 1
                if failure == "memory"
                else 12000
            },
        )
        if failure == "labels":
            windows[0]["window"]["end"] = windows[0]["window"]["start"]
            (target / "paired-source-labels.json").write_bytes(
                canonical_bytes({"complete_windows": windows, "episodes": []}) + b"\n"
            )
        return {"status": "passed", "sampled_tree_peak_rss_bytes": 12345}

    monkeypatch.setattr(runner, "monitor", run)
    return calls


def test_one_reserved_operation_charges_two_native_reads_and_one_total_cost(tmp_path, monkeypatch):
    value = case(tmp_path)
    calls = controlled(monkeypatch, value)
    bundle, receipt = execute(value)
    truth = runner.verify_campaign_paired_truth(bundle, journal=value[0], receipt=receipt)
    assert calls == [value[2]] and receipt.native_source_reads == 2
    assert receipt.plan.native_source_reads == 2 and not receipt.stage_ready
    assert truth.ordinary_source_dataset_id == value[4][0].source.parent.source_dataset_id
    assert truth.source_dataset_id == value[4][1].source.parent.source_dataset_id
    events = journal.inspect(value[0]).events
    assert len([e for e in events if e.kind == "reserved" and e.operation_id == value[2]]) == 1
    assert (
        events[-1].result == "completed"
        and events[-1].cost.artifact_bytes == receipt.artifact_bytes
    )
    assert events[-1].cost.peak_process_tree_rss_bytes == 12345


def test_unfinished_ordinary_parent_rejects_before_worker_and_spends_attempt(tmp_path, monkeypatch):
    value = case(tmp_path, ordinary_complete=False)
    calls = controlled(monkeypatch, value)
    with pytest.raises(SnapshotError, match="parent_not_completed_before_reservation"):
        execute(value)
    assert calls == [] and journal.inspect(value[0]).events[-1].result == "failed"
    with pytest.raises(ValueError, match="budget_exhausted"):
        execute(value)


@pytest.mark.parametrize("failure", ["worker", "memory", "labels"])
def test_failed_pair_retains_cost_and_cannot_refund_or_retry(tmp_path, monkeypatch, failure):
    value = case(tmp_path)
    calls = controlled(monkeypatch, value, failure=failure)
    with pytest.raises(SnapshotError, match="verification_failed|memory_budget|labels_binding"):
        execute(value)
    event = journal.inspect(value[0]).events[-1]
    assert calls == [value[2]] and event.result == "failed" and event.cost.wall_seconds > 0
    with pytest.raises(ValueError, match="budget_exhausted"):
        execute(value)


def test_resealed_truth_bundle_does_not_change_completed_evidence(tmp_path, monkeypatch):
    value = case(tmp_path)
    controlled(monkeypatch, value)
    bundle, receipt = execute(value)
    payload = runner.decode_json(runner.read_bytes(bundle, "truth.json"))
    payload["ordinary_generation_receipt_sha256"] = "0" * 64
    (bundle / "truth.json").write_bytes(canonical_bytes(payload) + b"\n")
    with pytest.raises(SnapshotError, match="bundle_inventory"):
        runner.verify_campaign_paired_truth(bundle, journal=value[0], receipt=receipt)
    files, size = runner._bundle_inventory(bundle, receipt.plan.max_truth_bytes + 4 * 1024**2)
    altered = receipt.model_copy(
        update={
            "artifact_files": files,
            "artifact_bytes": size,
            "truth_sha256": canonical_sha256(payload),
        }
    )
    with pytest.raises(SnapshotError, match="receipt_not_completed"):
        runner.verify_campaign_paired_truth(bundle, journal=value[0], receipt=altered)


def test_frozen_metadata_does_not_grant_final_source_access(tmp_path, monkeypatch):
    value = case(tmp_path, final=True)
    calls = controlled(monkeypatch, value)
    with pytest.raises((SnapshotError, ValueError), match="selection|portfolio|proof|bundle"):
        execute(value)
    assert calls == [] and journal.inspect(value[0]).events[-1].result == "failed"


def test_complete_final_selection_is_checked_before_worker_and_receipt_replay(
    tmp_path, monkeypatch
):
    value = case(tmp_path, final=True)
    calls = controlled(monkeypatch, value)
    checked = []

    def selected(*args):
        checked.append(len(calls))
        return "8" * 64, {}

    monkeypatch.setattr(runner, "verify_completed_campaign_selection", selected)
    bundle, receipt = execute(value)
    assert checked == [0] and receipt.selection_sha256 == "8" * 64
    runner.verify_campaign_paired_truth(bundle, journal=value[0], receipt=receipt)
    assert checked == [0, 1]
