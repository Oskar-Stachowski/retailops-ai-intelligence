"""Public v2 composition with declared parent/model controls, never Project evidence."""

from contextlib import closing

import pytest
from pydantic import ValidationError
from test_ai09_campaign_journal import selection as declared_selection
from test_campaign_evaluation import control as control
from test_campaign_evaluation import execute, unregistered_context_receipt
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_evaluation as runner
from retailops_ai.evaluation_campaign import campaign_evaluation_data as data
from retailops_ai.evaluation_campaign import campaign_evaluation_worker as worker
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_context_bundle import validate_completed_context
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_export import _store_receipt
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_robust_receipt import (
    CampaignForecastRobustEvaluationReceipt,
    parse_forecast_evaluation_receipt,
)
from retailops_ai.evaluation_campaign.campaign_segment_contract import CampaignSourceKeyAnnotation
from retailops_ai.evaluation_campaign.campaign_segments import SegmentCensus, context_from_inputs
from retailops_ai.evaluation_campaign.campaign_selected_robustness import SelectedRobustness
from retailops_ai.evaluation_campaign.campaign_selection_evidence import (
    verify_completed_campaign_selection,
)
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.source_snapshot.files import SnapshotError, file_hash

pytestmark = pytest.mark.parametrize("control", ["robust"], indirect=True)


@pytest.fixture
def declared_context(control, tmp_path, monkeypatch):
    """Freeze operation/policies first; real row joins with declared Source proof."""
    recipe = control["context_recipe"]
    plan = control["recipe"].resolve(control["configuration"])
    prep = tmp_path / "context-preparation"
    prep.mkdir(mode=0o700)
    population = worker.prepare(
        prep,
        {
            "dataset": str(control["dataset"]),
            "exported": control["exported"].model_dump(mode="json"),
        },
        plan,
    )
    template = unregistered_context_receipt(control)
    scope = template.scope.model_copy(
        update={"segment_policy_sha256": recipe.segment_policy.content_sha256()}
    )
    contexts = []
    with closing(worker._readonly(prep / "inputs.sqlite")) as db:
        for window in data.windows(db, plan):
            for record in window.records:
                annotation = CampaignSourceKeyAnnotation(
                    **record.row.model_dump(include=set(ForecastKey.model_fields)),
                    context_scope_sha256=scope.content_sha256(),
                    source_scenario_plan_sha256=None,
                    scenario="normal",
                    anomaly="unannotated",
                )
                contexts.append(
                    context_from_inputs(
                        record.row,
                        window.history,
                        None,
                        None,
                        annotation,
                        scope=scope,
                        policy=recipe.segment_policy,
                        example_sha256=record.example_sha256,
                        eligible=record.eligible,
                        exclusion_reasons=record.exclusion_reasons,
                    )
                )
    contexts.sort(key=membership_key)
    census_stream = SegmentCensus(scope, recipe.segment_policy)
    for ctx in contexts:
        census_stream.add(ctx)
    census = census_stream.finish(
        expected_rows=population["rows"],
        expected_eligible_rows=population["eligible_rows"],
        expected_keys_sha256=population["keys_sha256"],
        expected_eligible_keys_sha256=population["eligible_keys_sha256"],
    )
    bundle = tmp_path / "declared-context"
    bundle.mkdir(mode=0o700)
    for name in template.artifact_files:
        if name not in {"contexts.jsonl", "census.json"}:
            write(bundle / name, {"declared_source_parent_control": True})
    write(bundle / "census.json", census.model_dump(mode="json"))
    (bundle / "contexts.jsonl").touch(mode=0o600)
    (bundle / "contexts.jsonl").write_bytes(
        b"".join(canonical_bytes(ctx.model_dump(mode="json")) + b"\n" for ctx in contexts)
    )
    hashes = {name: file_hash(bundle, name)[1] for name in template.artifact_files}
    reservation = journal.reserve(control["root"], "declared-context")
    receipt = template.model_copy(
        update={
            "operation_id": "declared-context",
            "reservation_id": str(reservation.reservation_id),
            "recipe": recipe,
            "scope": scope,
            **{key: population[key] for key in runner.POPULATION},
            "context_trace_sha256": census.context_trace_sha256,
            "census_sha256": census.content_sha256(),
            "artifact_files": hashes,
            "artifact_sha256": canonical_sha256(hashes),
            "artifact_bytes": sum((bundle / name).stat().st_size for name in hashes),
        }
    )
    _store_receipt(control["root"], receipt)
    journal.finish(
        control["root"],
        str(reservation.reservation_id),
        result="completed",
        evidence_sha256=receipt.content_sha256(),
        cost=CampaignCost(
            wall_seconds=0.01,
            artifact_bytes=receipt.artifact_bytes,
        ),
    )
    monkeypatch.setattr(
        runner,
        "verify_campaign_context_bundle",
        lambda path, *, journal, receipt, selection_bundles: validate_completed_context(
            journal, receipt
        ),
    )
    return bundle, receipt, census


def robust_execute(control, declared_context, **changes):
    bundle, receipt, _ = declared_context
    return execute(
        control,
        **(
            {
                "raw_context_bundle": bundle,
                "raw_context_receipt": receipt,
                "uncertainty_policy": control["uncertainty_policy"],
            }
            | changes
        ),
    )


def test_public_complete_v2_has_every_raw_trial_selected_group_method_and_durable_parent(
    control,
    declared_context,
):
    bundle, receipt = robust_execute(control, declared_context)
    assert isinstance(receipt, CampaignForecastRobustEvaluationReceipt)
    runner.verify_campaign_forecast_evaluation(bundle, journal=control["root"], receipt=receipt)
    assert receipt.critical_segment_inventory_complete and receipt.block_uncertainty_complete
    assert (
        not receipt.quality_qualified and not receipt.stage_ready and not receipt.promotion_allowed
    )
    assert receipt.actual_index_passes == len(receipt.configuration.trials) + 1
    metrics = read(bundle / "metrics.json")
    groups = metrics["selected_robustness"]["groups"]
    assert len(groups) == len(declared_context[2].populations)
    assert any(group["rows"] == 0 for group in groups)
    assert all(len(group["uncertainty"]["methods"]) == 2 for group in groups)
    assert all(not group["uncertainty"]["quality_qualified"] for group in groups)
    assert (
        parse_forecast_evaluation_receipt(canonical_bytes(receipt.model_dump(mode="json")))
        == receipt
    )
    ledger = journal.inspect(control["root"])
    assert ledger.protocol == control["protocol"]
    assert ledger.protocol_sha256 == control["protocol"].content_sha256()
    assert ledger.events[-1].result == "completed"


@pytest.mark.parametrize(
    "attack", ["drop", "gate", "point", "seed", "scope", "policy", "qualify", "horizon"]
)
def test_resealed_bundle_rejects_missing_groups_and_changed_equations_or_bindings(
    control,
    declared_context,
    attack,
):
    bundle, receipt = robust_execute(control, declared_context)
    metrics = read(bundle / "metrics.json")
    report = metrics["selected_robustness"]
    group = report["groups"][0]
    if attack == "drop":
        report["groups"].pop()
    elif attack == "gate":
        group["comparison"]["status"] = "passed"
    elif attack == "point":
        group["uncertainty"]["methods"][0]["metrics"]["mean_mse_delta"]["point_delta"] = 999.0
    elif attack == "seed":
        group["uncertainty"]["methods"][0]["derived_resampling_seed"] = 0
    elif attack == "scope":
        group["uncertainty"]["scope"]["dimension"] = "category"
    elif attack == "policy":
        report["uncertainty_policy_sha256"] = "0" * 64
    elif attack == "qualify":
        report["quality_qualified"] = True
    else:
        metrics["segments"][0]["status"] = "passed"
    (bundle / "metrics.json").write_bytes(canonical_bytes(metrics) + b"\n")
    hashes = {name: file_hash(bundle, name)[1] for name in receipt.artifact_files}
    altered = receipt.model_copy(
        update={
            "artifact_files": hashes,
            "artifact_sha256": canonical_sha256(hashes),
            "artifact_bytes": sum((bundle / name).stat().st_size for name in hashes),
        }
    )
    with pytest.raises((SnapshotError, ValidationError)):
        runner._verify_bundle(bundle, altered)


@pytest.mark.parametrize("attack", ["no-context", "policy", "raw-parent"])
def test_full_policy_requires_frozen_actual_context_before_role_workers(
    control, declared_context, attack
):
    changes = {}
    if attack == "no-context":
        changes = {"raw_context_bundle": None, "raw_context_receipt": None}
    elif attack == "policy":
        changes = {
            "uncertainty_policy": control["uncertainty_policy"].model_copy(
                update={"resampling_seed": 42}
            )
        }
    else:
        changes = {
            "raw_context_receipt": declared_context[1].model_copy(
                update={"protocol_sha256": "0" * 64}
            )
        }
    with pytest.raises(SnapshotError):
        robust_execute(control, declared_context, **changes)
    assert not {"prepare", "predict", "consume", "finalize"}.intersection(control["calls"])
    assert journal.inspect(control["root"]).events[-1].result == "failed"


def test_context_checksum_change_fails_without_completed_evaluation_bundle(
    control, declared_context
):
    (declared_context[0] / "contexts.jsonl").write_bytes(b"changed\n")
    with pytest.raises(SnapshotError):
        robust_execute(control, declared_context)
    assert journal.inspect(control["root"]).events[-1].result == "failed"


def test_unknown_receipt_version_has_no_downgrade_path(control):
    with pytest.raises(SnapshotError, match="version_invalid"):
        parse_forecast_evaluation_receipt(canonical_bytes({"version": "ai09-unknown"}))


def test_complete_underpowered_v2_cannot_authorize_final_source_access(control, declared_context):
    bundle, receipt = robust_execute(control, declared_context)
    fields = declared_selection(control["root"]).model_dump(mode="json")
    fields["bundles"][0] |= receipt.selection_components.model_dump(mode="json") | {
        "selection_evidence_sha256": receipt.content_sha256(),
    }
    from retailops_ai.evaluation_campaign.campaign_contract import SelectionFreeze

    journal.freeze_selection(
        control["root"], SelectionFreeze.model_validate_json(canonical_bytes(fields))
    )
    with pytest.raises(SnapshotError, match="receipt_incomplete"):
        verify_completed_campaign_selection(
            control["root"], {use: bundle for use in ("forecast", "anomaly", "stockout")}
        )


def test_combined_sqlite_cap_does_not_drop_empty_declared_groups(
    control, declared_context, tmp_path
):
    root = tmp_path / "limited-indexes"
    root.mkdir(mode=0o700)
    bundle, receipt, census = declared_context
    plan = (
        control["recipe"]
        .resolve(control["configuration"])
        .model_copy(update={"max_index_bytes": 4096})
    )
    with pytest.raises(SnapshotError, match="combined_index_budget"):
        with SelectedRobustness(
            root,
            bundle,
            {
                "receipt": receipt.model_dump(mode="json"),
                "census": census.model_dump(mode="json"),
            },
            plan,
            {key: getattr(receipt, key) for key in runner.POPULATION},
            control["uncertainty_policy"],
            retained_median_baseline=False,
        ):
            raise AssertionError("an over-budget full inventory must not become available")
    assert len(list((root / "paired").glob("*.sqlite"))) == len(census.populations)


def test_selected_context_is_sealed_again_after_all_actual_keys(
    control, declared_context, monkeypatch
):
    original = SelectedRobustness.finish

    def changed(stream):
        path = declared_context[0] / "contexts.jsonl"
        raw = path.read_bytes()
        first = next(iter(raw.splitlines()))
        from retailops_ai.evaluation_campaign.campaign_segment_contract import (
            CampaignForecastKeyContext,
        )

        ctx = CampaignForecastKeyContext.model_validate_json(first)
        replacement = canonical_bytes(
            ctx.model_copy(update={"example_sha256": "0" * 64}).model_dump(mode="json")
        )
        path.write_bytes(replacement + raw[len(first) :])
        return original(stream)

    monkeypatch.setattr(SelectedRobustness, "finish", changed)
    with pytest.raises(SnapshotError, match="context_checksum"):
        robust_execute(control, declared_context)
    assert journal.inspect(control["root"]).events[-1].result == "failed"
