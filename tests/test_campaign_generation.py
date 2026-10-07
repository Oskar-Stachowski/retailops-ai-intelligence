"""Ordering and failure tests use controlled metadata, not a project campaign."""

import json
import os
import sys
from pathlib import Path
from time import perf_counter

import psutil
import pytest
from pydantic import ValidationError
from test_ai09_campaign_journal import protocol_document
from test_forecast_source_replay import physical_parents as physical_parents
from test_physical_forecast import source as declared_source

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_generation as runner
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import partitions
from retailops_ai.evaluation_campaign.campaign_contract import CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationPlan,
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor
from retailops_ai.evaluation_campaign.campaign_generation_worker import consumer, write
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
from retailops_ai.source_snapshot.files import SnapshotError, file_hash


def resources(**changes):
    return CampaignGenerationResources.model_validate(
        {
            "wall_seconds": 5,
            "tree_rss_bytes": 1024**3,
            "scratch_bytes": 1024**2,
            "minimum_free_disk_bytes": 1024,
            "minimum_available_memory_bytes": 1024,
            "sample_seconds": 0.02,
            **changes,
        }
    )


def campaign(tmp_path, *, final=False):
    root = tmp_path.resolve() / "journal"
    document = protocol_document(root)
    source = document["sources"][1 if final else 0]
    parameters = {
        "profile": source["profile"],
        "seed": source["seed"],
        "days": 730 if final else 365,
        "products": source["products"],
        "stores": source["selling_pairs"],
        "warehouses": source["stock_locations"],
        "start_date": source["history"]["start"],
        "end_date": source["history"]["end"],
        "business_timezone": "UTC",
        "output_format": "csv",
        "warmup_days": 0,
        "origin_days": 0,
        "label_tail_days": 0,
        "max_daily_rows": 1460000 if final else 182500,
    }
    old = canonical_sha256(source)
    source["exporter_lock_sha256"] = canonical_sha256("controlled-parquet-lock")
    source["generation_config_sha256"] = canonical_sha256(parameters)
    digest = canonical_sha256(source)
    operation_id = ("final" if final else "development") + "-42-generate"
    plan = CampaignGenerationPlan(
        source_recipe_sha256=digest,
        exporter_lock_sha256=source["exporter_lock_sha256"],
        requested_parameters=parameters,
        resolved_parameters=parameters,
        entrypoint="cached_inventory_v2",
        snapshot_schema_version="1.1.0",
        resources=resources(),
    )
    for operation in document["operations"]:
        if operation["source_recipe_sha256"] == old:
            operation["source_recipe_sha256"] = digest
        if operation["operation_id"] == operation_id:
            operation["execution_recipe_sha256"] = plan.content_sha256()
    protocol = CampaignProtocol.model_validate_json(canonical_bytes(document))
    journal.initialize(root, protocol)
    output = tmp_path.resolve() / "output"
    output.mkdir(mode=0o700)
    return root, output, operation_id, plan


def run(case):
    root, output, operation_id, plan = case
    return runner.generate_campaign_parent(
        Path("/never-read-producer"),
        Path(sys.executable),
        output,
        journal=root,
        operation_id=operation_id,
        plan=plan,
    )


def fake_phases(monkeypatch, case, *, failure=None):
    root, _, operation_id, plan = case
    phases = []
    monkeypatch.setattr(runner, "_producer_pin", lambda *args: None)
    spec = declared_source().model_copy(
        update={
            "source_parameters": plan.resolved_parameters,
            "schema_version": plan.snapshot_schema_version,
        }
    )

    def phase(command, **kwargs):
        assert journal.inspect(root).events[-1].kind == "reserved"
        assert journal.inspect(root).events[-1].operation_id == operation_id
        name = command[-3]
        phases.append(name)
        directory = kwargs["root"]
        if name == failure:
            return {
                "status": "failed",
                "reason": "worker_exit",
                "wall_seconds": 0.1,
                "sampled_tree_peak_rss_bytes": 8192,
            }
        value = {"worker_peak_rss_bytes": 4096}
        if name in {"import", "curation"}:
            destination = directory / name
            destination.mkdir(mode=0o700)
            if name == "import":
                (destination / "snapshot").mkdir(mode=0o700)
            value["destination"] = str(destination)
        if name == "verify":
            value["source"] = spec.model_dump(mode="json")
        write(directory / (name + ".json"), value)
        return {"status": "passed", "wall_seconds": 0.1, "sampled_tree_peak_rss_bytes": 8192}

    monkeypatch.setattr(runner, "monitor", phase)

    def imported_hash(root, name):
        if name.startswith("snapshot"):
            assert root.name == "snapshot" and root.parent.name == "import"
            assert root.is_dir(), "the public import destination wraps a snapshot directory"
        return (
            1,
            spec.snapshot_manifest_sha256
            if name.startswith("snapshot")
            else spec.curated_manifest_sha256,
        )

    monkeypatch.setattr(runner, "file_hash", imported_hash)
    return phases


def test_completion_requires_all_six_phases_and_durable_verified_receipt(tmp_path, monkeypatch):
    case = campaign(tmp_path)
    phases = fake_phases(monkeypatch, case)
    snapshot, _, receipt = run(case)
    assert snapshot.name == "snapshot" and snapshot.parent.name == "import"
    assert phases == list(runner.PHASES)
    runner.validate_completed_generation(case[0], receipt)
    finish = journal.inspect(case[0]).events[-1]
    assert finish.result == "completed" and finish.evidence_sha256 == receipt.content_sha256()
    assert finish.cost.peak_process_tree_rss_bytes == 8192
    assert finish.cost.artifact_bytes > 0
    stored = case[0] / "receipts" / (receipt.reservation_id + ".json")
    assert stored.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValidationError, match="budget_exhausted"):
        run(case)


def test_verify_replays_snapshot_inside_public_import_envelope(physical_parents, tmp_path):
    """Real public smoke import and complete replay; no new campaign or final data."""
    snapshot, curated = physical_parents
    metadata = json.loads((snapshot / "snapshot_manifest.json").read_bytes())
    write(tmp_path / "import.json", {"destination": str(snapshot.parent)})
    write(tmp_path / "curation.json", {"destination": str(curated)})
    assert not (snapshot.parent / "snapshot_manifest.json").exists()
    request = {
        "plan": {
            "parent_budget": {
                name: PhysicalSourceSpec.model_fields[name].default
                for name in (
                    "max_parent_bytes",
                    "max_parent_files",
                    "max_rows_per_parent",
                    "batch_rows",
                )
            },
            "snapshot_schema_version": metadata["schema_version"],
            "resolved_parameters": metadata["source"]["descriptor"]["resolved_parameters"],
            "exporter_lock_sha256": metadata["exporter"]["dependency_sha256"],
        },
        "source": {
            "producer_commit": metadata["source"]["provenance"]["git_commit"],
            "producer_lock_sha256": metadata["source"]["provenance"]["dependency_sha256"],
        },
        "runtime": partitions.runtime_pin().model_dump(mode="json"),
    }
    result = consumer("verify", tmp_path, request)
    spec = PhysicalSourceSpec.model_validate(result["source"])
    assert spec.parent.snapshot_id == metadata["snapshot_id"]
    assert spec.snapshot_manifest_sha256 == file_hash(snapshot, "snapshot_manifest.json")[1]
    assert spec.curated_manifest_sha256 == file_hash(curated, "curated_manifest.json")[1]
    assert set(result["verified_inventories"]) == {"snapshot", "curated", "logical_curated"}
    assert all(len(digest) == 64 for digest in result["verified_inventories"].values())


@pytest.mark.parametrize("phase", runner.PHASES)
def test_every_phase_failure_stays_charged_and_prevents_success(tmp_path, monkeypatch, phase):
    case = campaign(tmp_path)
    phases = fake_phases(monkeypatch, case, failure=phase)
    with pytest.raises(SnapshotError, match="phase_failed"):
        run(case)
    assert phases[-1] == phase
    end = journal.inspect(case[0]).events[-1]
    assert end.result == "failed" and end.cost.wall_seconds > 0
    assert end.cost.peak_process_tree_rss_bytes == 8192
    assert not (case[0] / "receipts").exists()
    with pytest.raises(ValidationError, match="budget_exhausted"):
        run(case)


def test_changed_frozen_plan_never_opens_producer_and_is_charged(tmp_path, monkeypatch):
    root, output, operation_id, plan = campaign(tmp_path)
    changed = plan.model_copy(update={"chunk_rows": 257})
    monkeypatch.setattr(
        runner, "_producer_pin", lambda *args: pytest.fail("producer read before binding")
    )
    with pytest.raises(SnapshotError, match="frozen_plan"):
        run((root, output, operation_id, changed))
    assert journal.inspect(root).events[-1].result == "failed"
    assert not list(output.iterdir())


def test_final_generation_is_blocked_before_freeze_without_io(tmp_path, monkeypatch):
    case = campaign(tmp_path, final=True)
    monkeypatch.setattr(runner, "_producer_pin", lambda *args: pytest.fail("final producer opened"))
    with pytest.raises(ValidationError, match="phase_mismatch"):
        run(case)
    assert not journal.inspect(case[0]).events
    assert not list(case[1].iterdir())


@pytest.mark.parametrize("operation", ["unknown", "development-fit", "development-42-read"])
def test_wrong_operation_does_not_consume_any_attempt(tmp_path, operation):
    root, output, _, plan = campaign(tmp_path)
    with pytest.raises(SnapshotError, match="source_generate"):
        run((root, output, operation, plan))
    assert not journal.inspect(root).events


def actual_monitor(tmp_path, program, *, limits=None, seconds=5):
    return monitor(
        [sys.executable, "-c", program],
        root=tmp_path,
        log=tmp_path / "worker.log",
        env=dict(os.environ),
        scratch=(tmp_path,),
        resources=limits or resources(),
        deadline=perf_counter() + seconds,
    )


def test_actual_worker_measures_tree_cost(tmp_path):
    result = actual_monitor(tmp_path, "import time; time.sleep(0.1)")
    assert result["status"] == "passed" and result["wall_seconds"] >= 0.1
    assert result["sampled_tree_peak_rss_bytes"] >= psutil.Process().memory_info().rss * 0.8


def test_actual_failed_worker_retains_measured_cost(tmp_path):
    result = actual_monitor(tmp_path, "raise SystemExit(7)")
    assert result["reason"] == "worker_exit" and result["exit_code"] == 7
    assert result["wall_seconds"] > 0 and result["samples"] > 0


def test_actual_wall_limit_kills_owned_children_and_leaves_caller_alive(tmp_path):
    result = actual_monitor(
        tmp_path,
        (
            "import subprocess,sys,time; from pathlib import Path; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            "Path('child.pid').write_text(str(p.pid)); time.sleep(30)"
        ),
        seconds=0.4,
    )
    assert result["reason"] == "wall_limit"
    pid = int((tmp_path / "child.pid").read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert psutil.Process().is_running()


def test_actual_scratch_limit_retains_failed_output(tmp_path):
    result = actual_monitor(
        tmp_path,
        (
            "from pathlib import Path; import time; "
            "Path('large').write_bytes(b'x' * 2 * 1024**2); time.sleep(30)"
        ),
    )
    assert result["reason"] == "scratch_limit"
    assert (tmp_path / "large").stat().st_size == 2 * 1024**2


def test_scratch_symlink_is_rejected_and_owned_worker_stops(tmp_path):
    (tmp_path / "link").symlink_to(tmp_path.parent)
    result = actual_monitor(tmp_path, "import time; time.sleep(30)")
    assert result["reason"] == "monitor_error" and result["exit_code"] != 0
