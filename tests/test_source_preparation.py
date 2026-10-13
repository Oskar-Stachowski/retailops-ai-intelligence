"""Real five-operation controllers and journals; native Source workers are explicit doubles."""

import hashlib
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import test_campaign_generation as generation_controls
from pydantic import ValidationError
from test_development_planning import controlled_worker
from test_development_variants import replay_worker

from retailops_ai.data_contracts.common import DateWindow, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import source_preparation as driver
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.canonical_development_contract import (
    PreparedCanonicalDevelopmentParents,
)
from retailops_ai.evaluation_campaign.development_policy_freeze import (
    DevelopmentPolicySet,
    FrozenDevelopmentPolicies,
)
from retailops_ai.evaluation_campaign.development_screening_contract import (
    PreparedDevelopmentParents,
)
from retailops_ai.evaluation_campaign.legacy_carryover import load_legacy_carryover
from retailops_ai.evaluation_campaign.partition_contract import (
    ROLES,
    ForecastPartitionPolicy,
    RoleWindow,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.preparation_execution import locked
from retailops_ai.evaluation_campaign.source_preparation_contract import (
    SourcePreparationOptions,
    SourcePreparationProducerPin,
)
from retailops_ai.source_snapshot.files import SnapshotError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup(tmp_path, monkeypatch, request):
    root = tmp_path.resolve() / "session"
    root.mkdir(mode=0o700)
    producer = tmp_path.resolve() / "declared-producer"
    producer.mkdir()
    policies = DevelopmentPolicySet.model_validate_json(
        (ROOT / "contracts/evaluation/v38/development_policies.default.json").read_bytes()
    )
    frozen = FrozenDevelopmentPolicies(
        policies=policies,
        policies_sha256=policies.content_sha256(),
        context=policies.context(load_legacy_carryover(ROOT / "docs/evidence"), runtime_pin()),
    )
    roles = []
    for i, name in enumerate(ROLES):
        start = date(2025, 9, 1) + timedelta(days=i * 46)
        end = start + timedelta(days=29)
        roles.append(
            RoleWindow(
                role=name,
                origins=DateWindow(start=start, end=end),
                label_knowledge_cutoff=end_of_day(end + timedelta(days=15)),
            )
        )
    resources = CampaignGenerationResources(
        wall_seconds=3600,
        tree_rss_bytes=512 * 1024**2,
        scratch_bytes=8 * 1024**3,
        minimum_available_memory_bytes=1024**3,
        minimum_free_disk_bytes=6 * 1024**3,
    )
    options = SourcePreparationOptions(
        products=getattr(request, "param", 25),
        producer=str(producer),
        producer_python=sys.executable,
        partitions=ForecastPartitionPolicy(roles=tuple(roles)),
        generation_resources=resources,
        planning_resources=resources,
        reuse_resources=resources,
    )
    state = {"calls": [], "fail": False}

    def metadata(options, frozen):
        state["calls"].append("metadata")
        return SourcePreparationProducerPin(
            producer_commit=frozen.policies.catalog.producer_commit,
            producer_lock_sha256="b" * 64,
            exporter_lock_sha256="c" * 64,
            producer_planner_sha256="d" * 64,
        )

    monkeypatch.setattr(driver, "inspect_preparation_producer", metadata)
    snapshot_bytes, curated_bytes = b'{"declared":"snapshot"}\n', b'{"declared":"curated"}\n'
    original_spec = generation_controls.declared_source()
    monkeypatch.setattr(
        generation_controls,
        "declared_source",
        lambda: original_spec.model_copy(
            update={
                "snapshot_manifest_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
                "curated_manifest_sha256": hashlib.sha256(curated_bytes).hexdigest(),
            }
        ),
    )
    ordinary_controller = driver.generate_ordinary_for_planning
    variant_controller = driver.generate_resolved_development_variant
    planning_controller = driver.plan_native_development_scenarios
    reuse_controller = driver.reuse_ordinary_development_parent

    def generate(*, variant=None, **kwargs):
        ledger = journal.inspect(kwargs["journal"])
        index = 0 if variant is None else {"demand": 1, "physical": 2}[variant]
        plan = (
            ledger.protocol.generation
            if variant is None
            else ledger.protocol.planned.preparation.generations[index]
        )
        state["calls"].append("generate-" + (variant or "ordinary"))
        generation_controls.fake_phases(
            monkeypatch,
            (
                kwargs["journal"],
                kwargs["output_root"],
                ledger.protocol.operations[index].operation_id,
                plan,
            ),
            failure="generation" if state["fail"] else None,
        )
        result = (
            ordinary_controller(**kwargs)
            if variant is None
            else variant_controller(variant=variant, **kwargs)
        )
        snapshot, curated, receipt = result
        (snapshot / "snapshot_manifest.json").write_bytes(snapshot_bytes)
        (curated / "curated_manifest.json").write_bytes(curated_bytes)
        base = kwargs["output_root"] / receipt.reservation_id
        raw = base / "raw/native-declared-source"
        raw.mkdir(parents=True, mode=0o700)
        (base / "generation.json").write_bytes(
            canonical_bytes(
                {
                    "directory": str(raw),
                    "source_dataset_id": receipt.source.parent.source_dataset_id,
                }
            )
        )
        return result

    def plan(**kwargs):
        state["calls"].append("plan")
        controlled_worker(monkeypatch, kwargs["journal"])
        return planning_controller(**kwargs)

    def reuse(**kwargs):
        state["calls"].append("reuse")
        replay_worker(
            monkeypatch,
            (
                kwargs["journal"],
                kwargs["output_root"],
                journal.inspect(kwargs["journal"]).protocol,
                kwargs["snapshot"],
                kwargs["curated"],
            ),
        )
        return reuse_controller(**kwargs)

    monkeypatch.setattr(driver, "generate_ordinary_for_planning", generate)
    monkeypatch.setattr(driver, "generate_resolved_development_variant", generate)
    monkeypatch.setattr(driver, "plan_native_development_scenarios", plan)
    monkeypatch.setattr(driver, "reuse_ordinary_development_parent", reuse)
    return root, options, frozen, state


def run(setup, count=5):
    root, options, policies, _ = setup
    return driver.run_source_preparation(root, options, policies, maximum_new_operations=count)


@pytest.mark.parametrize("setup", [25, 50, 100], indirect=True)
def test_all_five_original_operations_resume_without_workers_or_cost_reset(setup):
    root, options, _, state = setup
    result = run(setup)
    assert result == {
        "new_operations": 5,
        "completed_operations": 5,
        "complete": True,
        "model_fit_authorized": False,
        "final_test_access_authorized": False,
        "stage_ready": False,
    }
    assert state["calls"] == [
        "metadata",
        "generate-ordinary",
        "plan",
        "reuse",
        "generate-demand",
        "generate-physical",
    ]
    raw = (root / "source-preparation-result.json").read_bytes()
    value = json.loads(raw)
    kind = (
        PreparedCanonicalDevelopmentParents
        if options.products == 100
        else PreparedDevelopmentParents
    )
    parents = kind.model_validate_json(canonical_bytes(value["parents"]))
    assert parents.cold_wall_seconds() == pytest.approx(
        sum(
            event.cost.wall_seconds
            for directory in ("planning", "variants")
            for event in journal.inspect(root / directory).events
            if event.kind == "finished"
        )
    )
    assert tuple(value["parent_paths"]) == ("demand", "ordinary", "physical")
    original = {name: journal.inspect(root / name) for name in ("planning", "variants")}
    calls = list(state["calls"])
    assert run(setup)["new_operations"] == 0
    assert state["calls"] == calls and (root / "source-preparation-result.json").read_bytes() == raw
    assert original == {name: journal.inspect(root / name) for name in original}


def test_clean_stop_between_each_operation_preserves_original_events(setup):
    root, _, _, state = setup
    assert run(setup, 1)["completed_operations"] == 1
    original = journal.inspect(root / "planning").events
    for count in range(2, 6):
        result = run(setup, 1)
        assert result["completed_operations"] == count and result["new_operations"] == 1
        assert result["complete"] == (count == 5)
        assert journal.inspect(root / "planning").events[:2] == original
    assert len(state["calls"]) == 6


@pytest.mark.parametrize(
    "attack",
    [
        "options",
        "runtime",
        "failed",
        "unfinished",
        "missing_receipt",
        "manifest",
        "escaped_raw",
        "owner",
    ],
)
def test_changed_or_unfinished_state_blocks_new_source_work(setup, attack):
    root, options, policies, state = setup
    if attack == "failed":
        state["fail"] = True
        with pytest.raises(SnapshotError, match="phase_failed"):
            run(setup, 1)
    else:
        run(setup, 1)
    original = journal.inspect(root / "planning")
    if attack == "options":
        options = options.model_copy(
            update={"producer_python": options.producer_python + "-changed"}
        )
    elif attack == "runtime":
        policies = policies.model_copy(
            update={
                "context": policies.context.model_copy(
                    update={
                        "runtime": policies.context.runtime.model_copy(
                            update={"code_sha256": "0" * 64}
                        )
                    }
                )
            }
        )
    elif attack == "unfinished":
        journal.reserve(root / "planning", original.protocol.operations[1].operation_id)
    elif attack == "missing_receipt":
        (root / "planning/receipts" / (original.events[0].reservation_id + ".json")).unlink()
    elif attack in {"manifest", "escaped_raw"}:
        base = root / "artifacts" / original.events[0].reservation_id
        if attack == "manifest":
            (base / "import/snapshot/snapshot_manifest.json").write_bytes(b"changed")
        else:
            value = json.loads((base / "generation.json").read_bytes())
            value["directory"] = str(root.parent)
            (base / "generation.json").write_bytes(canonical_bytes(value))
    before = list(state["calls"])
    if attack == "owner":
        with locked(root), pytest.raises(ValueError, match="still_active"):
            driver.run_source_preparation(root, options, policies)
    else:
        with pytest.raises((SnapshotError, ValueError, FileNotFoundError)):
            driver.run_source_preparation(root, options, policies)
    assert state["calls"] == before


def test_session_cannot_adopt_existing_journal_or_overlap_producer(setup):
    root, options, policies, state = setup
    (root / "planning").mkdir()
    with pytest.raises(SnapshotError, match="precede_all_operations"):
        run(setup)
    assert state["calls"] == []
    (root / "planning").rmdir()
    with pytest.raises(SnapshotError, match="overlaps_producer"):
        driver.run_source_preparation(
            root, options.model_copy(update={"producer": str(root.parent)}), policies
        )


def test_resource_ceiling_cannot_remove_host_reserve(setup):
    _, options, _, _ = setup
    value = options.model_dump(mode="json")
    value["generation_resources"]["minimum_available_memory_bytes"] = 1024
    with pytest.raises(ValidationError, match="resource_ceiling"):
        SourcePreparationOptions.model_validate_json(canonical_bytes(value))


@pytest.mark.parametrize(
    "attack", ["planning_journal", "variant_journal", "native_plan", "result", "failed_variant"]
)
def test_lost_or_failed_preparation_cannot_recreate_attempts(setup, attack):
    root, _, _, state = setup
    run(setup, 5 if attack == "result" else 3)
    if attack.endswith("journal"):
        shutil.rmtree(root / ("planning" if attack == "planning_journal" else "variants"))
    elif attack == "native_plan":
        event = journal.inspect(root / "planning").events[-1]
        (root / "artifacts" / event.reservation_id / "native-plans.json").write_bytes(b"{}\n")
    elif attack == "result":
        (root / "source-preparation-result.json").write_bytes(b"{}\n")
    else:
        state["fail"] = True
        with pytest.raises(SnapshotError, match="phase_failed"):
            run(setup)
    before = list(state["calls"])
    with pytest.raises((SnapshotError, ValueError)):
        run(setup)
    assert state["calls"] == before
