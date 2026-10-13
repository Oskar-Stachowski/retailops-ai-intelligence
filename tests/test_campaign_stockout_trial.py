"""Real fixture fits and cold workers with synthetic parents and explicit monitor doubles.

The role files contain controlled numerical examples, not generated Project
data. Durable Source/preparation receipts and monitor costs are test doubles.
The real loaders, learners, artifacts, journal and separate reload processes run.
"""

import hashlib
import subprocess
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_campaign_role_protocol import role_document
from test_campaign_stockout_maturity import feature, label
from test_campaign_stockout_preparation import limits
from test_physical_forecast import source as declared_source

from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import campaign_stockout_role_artifact as artifact
from retailops_ai.evaluation_campaign import campaign_stockout_trial as component
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_export import MAX_RECEIPT_BYTES, _store_receipt
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_fit_artifact import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_role_protocol import RoleAwareCampaignProtocol
from retailops_ai.evaluation_campaign.campaign_stockout_maturity import _membership
from retailops_ai.evaluation_campaign.campaign_stockout_maturity_contract import (
    STOCKOUT_ROLES,
    CampaignStockoutMaturityPolicy,
)
from retailops_ai.evaluation_campaign.campaign_stockout_preparation_contract import (
    CampaignStockoutPreparationPlan,
    CampaignStockoutPreparationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_stockout_role_contract import (
    FILES,
    MEMBERSHIP,
    ROLE_FILES,
)
from retailops_ai.evaluation_campaign.campaign_stockout_trial_artifact import verify_trial_artifact
from retailops_ai.evaluation_campaign.campaign_stockout_trial_contract import (
    CampaignStockoutTrialPlan,
)
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
from retailops_ai.source_snapshot.files import SnapshotError


def synthetic_roles(root, plan, *, reverse_calibration=False):
    """A deliberately synthetic complete file fixture, never Source ancestry evidence."""
    root.mkdir(mode=0o700)
    bodies = {name: bytearray() for name in FILES}
    traces = {name: artifact._Trace(labels=name != MEMBERSHIP) for name in FILES}
    for product in range(80):
        for position, window in enumerate(plan.maturity.policy.windows):
            origin = end_of_day(window.origins.start)
            product_id, outcome = f"p-{product:04d}", product % 2
            f = feature(origin, product=product_id)
            quantity = 10 + product // 2 if outcome else 100 + product // 2
            values = type(f.values).model_validate(
                {
                    **f.values.model_dump(mode="python"),
                    "available_qty": quantity,
                    "days_of_supply_observed": float(quantity),
                    "days_of_supply_in_stock": float(quantity),
                    "forecast_days_of_supply": float(quantity),
                }
            )
            f = f.model_copy(update={"values": values})
            if reverse_calibration and position == 1:
                outcome = 1 - outcome
            point = label(
                origin,
                product=product_id,
                incident_stockout=outcome,
                first_incident_at=origin + timedelta(days=1) if outcome else None,
                first_incident_event_id="controlled-event" if outcome else None,
            )
            row = artifact._joint(
                dict(
                    feature=f.model_dump(mode="json"),
                    label=point.model_dump(mode="json"),
                    membership=_membership(f, point, plan.maturity.policy),
                ),
                plan,
            )
            assert row.membership.eligible
            compact = artifact._compact(row)
            raw = canonical_bytes(compact.model_dump(mode="json")) + b"\n"
            bodies[MEMBERSHIP].extend(raw)
            traces[MEMBERSHIP].add(raw, artifact._key(compact), compact.joined_row_sha256)
            name = ROLE_FILES[STOCKOUT_ROLES[position]]
            raw = canonical_bytes(row.model_dump(mode="json")) + b"\n"
            bodies[name].extend(raw)
            traces[name].add(raw, artifact._key(compact), compact.joined_row_sha256, outcome)
    files = {name: trace.receipt() for name, trace in traces.items()}
    flags = dict(
        journal_authorization_proven=False,
        source_generation_ancestry_verified=False,
        quality_qualified=False,
        stage_ready=False,
    )
    maturity = dict(
        version="ai09-stockout-full-mature-development-receipt-1.0.0",
        rows=400,
        fitted_models=0,
        **flags,
        plan_sha256=canonical_sha256(plan.maturity.model_dump(mode="json")),
        public_dataset_id=plan.maturity.public_dataset_id,
        mature_role_membership_verified=True,
        complete_physical_origin_join_verified=True,
        original_labels=dict(
            rows=400,
            plan_sha256=canonical_sha256(plan.maturity.labels.model_dump(mode="json")),
            keys_sha256=files[MEMBERSHIP]["origin_stream_sha256"],
        ),
        eligible_by_role={r: 80 for r in STOCKOUT_ROLES},
        eligible_role_traces={
            r: dict(
                keys_sha256=files[ROLE_FILES[r]]["keys_sha256"],
                labels_sha256=files[ROLE_FILES[r]]["labels_sha256"],
                joined_rows_sha256=files[ROLE_FILES[r]]["sha256"],
            )
            for r in STOCKOUT_ROLES
        },
        classes_by_role={r: {"0": 40, "1": 40} for r in STOCKOUT_ROLES},
        ineligible_reasons={},
    )
    descriptor = dict(
        version="ai09-stockout-full-role-artifact-1.0.0",
        plan=plan.model_dump(mode="json"),
        maturity_receipt=maturity,
        files=files,
    )
    manifest = dict(
        dataset_id="ai09-stockout-roles-sha256-" + canonical_sha256(descriptor),
        descriptor=descriptor,
        **flags,
    )
    for name, body in bodies.items():
        (root / name).write_bytes(body)
        (root / name).chmod(0o600)
    write(root / "manifest.json", manifest)
    artifact.verify_stockout_roles(
        root,
        expected_dataset_id=manifest["dataset_id"],
        expected_plan=plan,
        allow_evaluation_truth=True,
    )
    return manifest


def setup(tmp_path, *, reverse_calibration=False):
    root = tmp_path / "journal"
    document = role_document(root)
    source = document["sources"][2]
    parameters = dict(
        profile="ai-dev",
        seed=42,
        days=365,
        products=100,
        stores=5,
        warehouses=3,
        start_date=source["history"]["start"],
        end_date=source["history"]["end"],
        business_timezone="UTC",
    )
    replacements = {}
    for variant in document["sources"][:3]:
        old = canonical_sha256(variant)
        variant["generation_config_sha256"] = canonical_sha256(parameters)
        replacements[old] = canonical_sha256(variant)
    for operation in document["operations"]:
        operation["source_recipe_sha256"] = replacements.get(
            operation["source_recipe_sha256"], operation["source_recipe_sha256"]
        )
    document["training_source_recipe_sha256"] = {
        case: replacements[value]
        for case, value in document["training_source_recipe_sha256"].items()
    }
    digest = canonical_sha256(source)
    gen_id, read_id = "development-42-physical-generate", "development-42-physical-read"
    preparation = CampaignStockoutPreparationPlan(
        source_recipe_sha256=digest,
        generation_operation_id=gen_id,
        policy=CampaignStockoutMaturityPolicy.model_validate_json(
            canonical_bytes({"windows": document["development_roles"]["windows"]})
        ),
        resources=limits(),
    )
    next(o for o in document["operations"] if o["operation_id"] == read_id)[
        "execution_recipe_sha256"
    ] = preparation.content_sha256()
    plans = {}
    for operation in document["operations"]:
        if (
            operation["phase"] != "development"
            or operation["use_case"] != "stockout"
            or operation["action"] not in ("model_fit", "calibrator_fit")
        ):
            continue
        conditional = operation["action"] == "calibrator_fit"
        plan = CampaignStockoutTrialPlan(
            source_recipe_sha256=digest,
            preparation_operation_id=read_id,
            kind="conditional" if conditional else "base",
            family=operation["stockout_family"],
            variant=operation["stockout_variant"],
            base_operation_id=operation["resolved_prerequisites"][0] if conditional else None,
            calibrator_C=operation.get("stockout_calibrator_C"),
            resources=limits(),
        )
        operation["execution_recipe_sha256"] = plan.content_sha256()
        plans[operation["operation_id"]] = plan
    protocol = RoleAwareCampaignProtocol.model_validate_json(canonical_bytes(document))
    journal.initialize(root, protocol)
    reservation = journal.reserve(root, gen_id)
    specification = declared_source().model_dump(mode="python")
    specification["source_parameters"] = parameters
    generated = CampaignGeneratedParentReceipt(
        protocol_sha256=protocol.content_sha256(),
        source_recipe_sha256=digest,
        operation_id=gen_id,
        reservation_id=str(reservation.reservation_id),
        source=PhysicalSourceSpec.model_validate(specification),
        runtime=protocol.runtime,
    )
    _store_receipt(root, generated)
    journal.finish(
        root,
        str(reservation.reservation_id),
        result="completed",
        evidence_sha256=generated.content_sha256(),
        cost=CampaignCost(wall_seconds=0.01),
    )
    reservation = journal.reserve(root, read_id)
    public_id = "ai09-stockout-public-sha256-" + "a" * 64
    role_plan = preparation.role_plan(generated, public_id)
    dataset = tmp_path / "roles"
    manifest = synthetic_roles(dataset, role_plan, reverse_calibration=reverse_calibration)
    files, size = _bundle_inventory(dataset, preparation.max_role_bytes)
    prepared = CampaignStockoutPreparationReceipt(
        protocol_sha256=protocol.content_sha256(),
        operation_id=read_id,
        reservation_id=str(reservation.reservation_id),
        plan=preparation,
        generated=generated,
        role_plan=role_plan,
        public_dataset_id=public_id,
        dataset_id=manifest["dataset_id"],
        manifest_sha256=files["manifest.json"],
        artifact_bytes=size,
        artifact_files=files,
        worker_evidence={
            "scope": "explicit synthetic fixture and metadata receipts, no actual Source generation"
        },
    )
    _store_receipt(root, prepared)
    journal.finish(
        root,
        str(reservation.reservation_id),
        result="completed",
        evidence_sha256=prepared.content_sha256(),
        cost=CampaignCost(wall_seconds=0.01),
    )
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    return SimpleNamespace(
        journal=root, plans=plans, prepared=prepared, dataset=dataset, output=output
    )


def invoke(case, operation_id, *, base=None, base_bundle=None):
    return component.run_campaign_stockout_trial(
        case.dataset,
        case.output,
        journal=case.journal,
        operation_id=operation_id,
        plan=case.plans[operation_id],
        prepared=case.prepared,
        base=base,
        base_bundle=base_bundle,
    )


def actual_worker_monitor_double(monkeypatch, *, failure=None, peak=8192):
    """Only the supervising cost result is doubled; execute actual cold workers."""
    calls = []

    def monitored(command, *, root, env, **kwargs):
        phase = command[-2]
        request = read(root / "request.json")
        ledger = journal.inspect(root.parents[1] / "journal")
        assert ledger.events[-1].kind == "reserved"
        calls.append((request["operation_id"], phase))
        if phase == failure:
            return dict(
                status="failed", sampled_tree_peak_rss_bytes=peak, reason="controlled_failure"
            )
        # Observe actual open events inside the new interpreter. Opening any
        # unselected role's payload (including membership) is an assertion failure.
        role = "base_train" if request["plan"]["kind"] == "base" else "calibrator_fit"
        script = "\n".join(
            [
                "import json,os,runpy,sys",
                "from pathlib import Path",
                "allowed=" + repr(role + ".jsonl"),
                "roles=" + repr(FILES),
                "opened=[]",
                "def audit(event,args):",
                " if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):",
                "  name=Path(os.fsdecode(args[0])).name",
                "  if name in roles:",
                "   assert name==allowed, 'unauthorized role '+name",
                "   opened.append(name)",
                "sys.addaudithook(audit)",
                "sys.argv=" + repr([command[3], phase, str(root)]),
                "if allowed=='calibrator_fit.jsonl':",
                " sys.path.insert(0,str(Path(sys.argv[0]).resolve().parents[2]))",
                " from retailops_ai.evaluation_campaign import campaign_stockout_training as training",
                " def no_refit(*a,**k): raise AssertionError('calibrator refitted base')",
                " training.fit_model=no_refit",
                "runpy.run_path(sys.argv[0],run_name='__main__')",
                "assert opened==[allowed], opened",
            ]
        )
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        (root / (phase + ".log")).write_text(result.stdout + result.stderr)
        (root / (phase + ".log")).chmod(0o600)
        assert result.returncode in (0, 1), (phase, result.stdout, result.stderr)
        if result.returncode != 0:
            return dict(status="failed", sampled_tree_peak_rss_bytes=peak, reason="worker_exit")
        return dict(
            status="passed", sampled_tree_peak_rss_bytes=peak, reason="explicit_monitor_cost_double"
        )

    monkeypatch.setattr(component, "monitor", monitored)
    return calls


@pytest.fixture(scope="module")
def completed_trials(tmp_path_factory):
    case = setup(tmp_path_factory.mktemp("stockout-real-trial-controls"))
    completed = {}
    with pytest.MonkeyPatch.context() as patch:
        calls = actual_worker_monitor_double(patch)
        for operation_id, plan in case.plans.items():
            parent = completed.get(plan.base_operation_id)
            completed[operation_id] = invoke(
                case,
                operation_id,
                base_bundle=parent[0] if parent else None,
                base=parent[1] if parent else None,
            )
    return case, completed, calls


def test_all_six_bases_and_twenty_four_calibrators_use_original_roles_and_cold_reload(
    completed_trials,
):
    case, completed, calls = completed_trials
    assert len(completed) == 30 and len(calls) == 60
    for operation_id, (bundle, receipt) in completed.items():
        component.validate_completed_stockout_trial(case.journal, receipt)
        model = verify_trial_artifact(bundle, receipt)
        assert receipt.role_file.rows == 80
        assert not receipt.quality_qualified and not receipt.stage_ready
        assert set(p.name for p in bundle.iterdir()) == {"pipeline.json", "binding.json"}
        assert calls.count((operation_id, "fit")) == calls.count((operation_id, "reload")) == 1
        assert receipt.worker_evidence["phases"][-1]["worker"]["cold_reload_passed"] is True
        assert len(canonical_bytes(receipt.model_dump(mode="json")) + b"\n") <= MAX_RECEIPT_BYTES
        worker_receipt_sha256 = receipt.model_copy(update={"worker_evidence": {}}).content_sha256()
        for phase in receipt.worker_evidence["phases"]:
            assert "receipt" not in phase["worker"]
            assert phase["worker"]["receipt_sha256"] == worker_receipt_sha256
            assert phase["worker"]["worker_peak_rss_bytes"] > 0
        if receipt.plan.kind == "conditional":
            base = completed[receipt.plan.base_operation_id][1]
            assert receipt.base_receipt_sha256 == base.content_sha256()
            assert (
                hashlib.sha256(
                    canonical_bytes(model.base.model_dump(mode="json")) + b"\n"
                ).hexdigest()
                == base.pipeline_sha256
            )
    assert len([e for e in journal.inspect(case.journal).events if e.kind == "finished"]) == 32


def test_completed_trial_cannot_run_again_or_refund_costs(completed_trials, monkeypatch):
    case, _, _ = completed_trials
    before = journal.inspect(case.journal)
    monkeypatch.setattr(component, "monitor", lambda *a, **k: pytest.fail("repeated fit"))
    with pytest.raises((SnapshotError, ValueError)):
        invoke(case, "stockout-base-0")
    assert journal.inspect(case.journal) == before


def test_changed_receipt_between_fit_and_reload_cannot_complete(tmp_path, monkeypatch):
    case = setup(tmp_path)
    calls = actual_worker_monitor_double(monkeypatch)
    actual_monitor = component.monitor

    def changed_monitor(command, *, root, **kwargs):
        measured = actual_monitor(command, root=root, **kwargs)
        if command[-2] == "reload" and measured["status"] == "passed":
            path = root / "reload.json"
            result = read(path)
            result["receipt"]["worker_evidence"] = {"changed_after_reload": True}
            path.write_bytes(canonical_bytes(result) + b"\n")
        return measured

    monkeypatch.setattr(component, "monitor", changed_monitor)
    with pytest.raises(SnapshotError, match="stockout_trial_worker_receipt_mismatch"):
        invoke(case, "stockout-base-0")
    assert calls == [("stockout-base-0", "fit"), ("stockout-base-0", "reload")]
    event = journal.inspect(case.journal).events[-1]
    assert event.result == "failed" and event.evidence_sha256 is None
    assert event.cost.wall_seconds > 0 and event.cost.peak_process_tree_rss_bytes > 8192
    assert not (case.journal / "receipts" / (str(event.reservation_id) + ".json")).exists()


@pytest.mark.parametrize("change", ["plan", "preparation", "stored_preparation"])
def test_bad_binding_is_charged_before_role_reads(tmp_path, monkeypatch, change):
    case = setup(tmp_path)
    if change == "plan":
        case.plans["stockout-base-0"] = case.plans["stockout-base-0"].model_copy(
            update={"variant": "raw_sales_only"}
        )
    elif change == "preparation":
        case.prepared = case.prepared.model_copy(update={"protocol_sha256": "f" * 64})
    else:
        p = case.journal / "receipts" / (case.prepared.reservation_id + ".json")
        p.write_bytes(p.read_bytes() + b" ")
    monkeypatch.setattr(
        component, "monitor", lambda *a, **k: pytest.fail("invalid binding reached worker")
    )
    with pytest.raises((SnapshotError, ValueError)):
        invoke(case, "stockout-base-0")
    event = journal.inspect(case.journal).events[-1]
    assert event.result == "failed" and event.evidence_sha256 is None
    assert event.cost.peak_process_tree_rss_bytes is None and not list(case.output.iterdir())


@pytest.mark.parametrize("peak", [None, 8192])
def test_failed_base_and_all_its_calibrators_remain_charged(tmp_path, monkeypatch, peak):
    case = setup(tmp_path)
    calls = actual_worker_monitor_double(monkeypatch, failure="fit", peak=peak)
    with pytest.raises(SnapshotError, match="phase_failed_fit"):
        invoke(case, "stockout-base-0")
    event = journal.inspect(case.journal).events[-1]
    assert event.cost.peak_process_tree_rss_bytes == peak
    for i in range(4):
        with pytest.raises(SnapshotError, match="original_completed_base_required"):
            invoke(case, f"stockout-calibrate-0-{i}")
    assert calls == [("stockout-base-0", "fit")]
    finished = [e for e in journal.inspect(case.journal).events if e.kind == "finished"][-5:]
    assert all(e.result == "failed" and e.evidence_sha256 is None for e in finished)
    assert all(e.cost.wall_seconds > 0 for e in finished)


def test_changed_model_or_receipt_blocks_acceptance(completed_trials, tmp_path):
    import shutil

    case, completed, _ = completed_trials
    bundle, receipt = completed["stockout-base-0"]
    changed = tmp_path / "bundle"
    shutil.copytree(bundle, changed)
    (changed / "pipeline.json").write_bytes((changed / "pipeline.json").read_bytes() + b" ")
    with pytest.raises(SnapshotError, match="checksum_mismatch"):
        verify_trial_artifact(changed, receipt)
    path = case.journal / "receipts" / (receipt.reservation_id + ".json")
    raw = path.read_bytes()
    try:
        path.write_bytes(raw + b" ")
        with pytest.raises(SnapshotError, match="stored_receipt_mismatch"):
            component.validate_completed_stockout_trial(case.journal, receipt)
    finally:
        path.write_bytes(raw)


def test_nonmonotone_calibrator_keeps_actual_rejection_and_cost_without_refitting_base(
    tmp_path, monkeypatch
):
    case = setup(tmp_path, reverse_calibration=True)
    calls = actual_worker_monitor_double(monkeypatch)
    bundle, base = invoke(case, "stockout-base-0")
    before = (bundle / "pipeline.json").read_bytes()
    for i in range(4):
        with pytest.raises(SnapshotError, match="stockout_conditional_nonpositive_raw_score_slope"):
            invoke(case, f"stockout-calibrate-0-{i}", base=base, base_bundle=bundle)
        event = journal.inspect(case.journal).events[-1]
        assert event.result == "failed" and event.evidence_sha256 is None
        assert event.error_code == "stockout_conditional_nonpositive_raw_score_slope"
        assert event.cost.wall_seconds > 0 and event.cost.peak_process_tree_rss_bytes > 8192
    assert (bundle / "pipeline.json").read_bytes() == before
    assert calls.count(("stockout-base-0", "fit")) == 1
    assert not any(
        phase == "reload" and operation != "stockout-base-0" for operation, phase in calls
    )


@pytest.mark.parametrize(
    "updates",
    [{"calibrator_C": True}, {"resources": {"wall_seconds": 1201}}, {"max_artifact_bytes": True}],
)
def test_invalid_python_model_copy_cannot_become_a_frozen_plan(tmp_path, updates):
    case = setup(tmp_path)
    plan = case.plans["stockout-calibrate-0-0"]
    if "resources" in updates:
        updates = {"resources": plan.resources.model_copy(update=updates["resources"])}
    with pytest.raises(ValueError):
        plan.model_copy(update=updates).content_sha256()
