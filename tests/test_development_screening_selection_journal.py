"""Full controller/journal controls with declared fit doubles, never Project evidence."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from test_campaign_score import fake_phases
from test_campaign_score_worker import fake_fits
from test_development_screening import export, native_double, remove_original, setup

from retailops_ai.evaluation_campaign import campaign_export as exporter
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import campaign_score as scorer
from retailops_ai.evaluation_campaign import campaign_tune as tuner
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_fit import validate_completed_fit
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_score_contract import FAMILIES
from retailops_ai.evaluation_campaign.campaign_tune_data import BandMetrics, choose
from retailops_ai.evaluation_campaign.development_screening_selection import rank_screening_families
from retailops_ai.evaluation_campaign.development_screening_selection_contract import (
    freeze_screening_selection,
)
from retailops_ai.source_snapshot.files import SnapshotError


def controlled_parents(tmp_path, monkeypatch, *, protocol_factory=None, score_phases=None):
    _, prefix, output = setup(tmp_path, monkeypatch)
    root = tmp_path / "screening-with-selection"
    prefix = prefix.model_copy(update={"journal_path": str(root)})
    protocol = (protocol_factory or freeze_screening_selection)(prefix)
    prefix = protocol.screening
    journal.initialize(root, protocol)
    case = root, protocol.screening, output
    exports = []
    datasets = []
    for i in range(3):
        native_double(monkeypatch, case, i)
        dataset, receipt = export(case, i)
        datasets.append(dataset)
        exports.append(receipt)
    prototype = fake_fits(exports[0].dataset_id)
    fits = {}
    for op, plan in zip(protocol.operations[3:9], prefix.fits, strict=True):
        reserved = journal.reserve(root, op.operation_id)
        receipt = prototype[plan.family].model_copy(
            update={
                "operation_id": op.operation_id,
                "reservation_id": str(reserved.reservation_id),
                "plan": plan,
                "protocol_sha256": protocol.content_sha256(),
                "runtime_code_sha256": protocol.runtime.code_sha256,
                "export_receipt_sha256": exports[0].content_sha256(),
                "dataset_id": exports[0].dataset_id,
            }
        )
        exporter._store_receipt(root, receipt)
        journal.finish(
            root,
            str(reserved.reservation_id),
            result="completed",
            evidence_sha256=receipt.content_sha256(),
            cost=CampaignCost(
                wall_seconds=0.01,
                peak_process_tree_rss_bytes=12345,
                artifact_bytes=receipt.model_artifact_bytes,
            ),
        )
        fits[op.operation_id] = receipt
    scores, bundles = {}, {}

    def declared_fit_verification(bundle, *, journal, receipt):
        # Models are explicit doubles. Original receipt/journal validation remains real.
        assert journal == root
        validate_completed_fit(root, receipt)

    monkeypatch.setattr(scorer, "verify_campaign_forecast_bundle", declared_fit_verification)
    for op, plan in zip(protocol.operations[9:11], prefix.scores, strict=True):
        selected = {f: fits[plan.fit_operation_ids[f]] for f in FAMILIES}
        (score_phases or fake_phases)(
            monkeypatch, (root, datasets[0], output, plan, exports[0], selected)
        )
        bundle, receipt = scorer.score_campaign_forecast(
            datasets[0],
            {f: output / ("declared-" + f) for f in FAMILIES},
            Path("/unused-controlled-python"),
            output,
            journal=root,
            operation_id=op.operation_id,
            plan=plan,
            exported=exports[0],
            fits=selected,
        )
        scores[op.operation_id], bundles[op.operation_id] = receipt, bundle
    return root, protocol, output, datasets[0], exports, fits, scores, bundles


def controlled_selection_worker(monkeypatch, case, fault=None):
    root, protocol, _, _, exports, _, scores, bundles = case
    plan = protocol.selection_plan
    calls = []

    def monitor(command, **kwargs):
        assert journal.inspect(root).events[-1].kind == "reserved"
        folder = kwargs["root"]
        assert read(folder / "request.json")["family_screening"] is True
        calls.append("selection-worker")
        trials = []
        for op in plan.score_operation_ids:
            score = scores[op]
            metrics = read(bundles[op] / "metrics.json")
            for segment in metrics["segments"]:
                for model in segment["models"].values():
                    model["interval"] = BandMetrics().result(segment["eligible_rows"])
            trials.append(
                {
                    "score_operation_id": op,
                    "metrics": metrics,
                    **{
                        k: getattr(score, k)
                        for k in (
                            "rows",
                            "eligible_rows",
                            "keys_sha256",
                            "eligible_keys_sha256",
                            "role_population_sha256",
                        )
                    },
                    "baseline_predictions_sha256": "4" * 64,
                }
            )
        selected = choose(trials, scores, plan)
        ranking = rank_screening_families(trials, scores, plan)
        bundle = folder / "bundle"
        bundle.mkdir(mode=0o700)
        write(bundle / "plan.json", plan.model_dump(mode="json"))
        write(
            bundle / "parents.json",
            {
                "export_receipt_sha256": exports[0].content_sha256(),
                "scores": {k: s.model_dump(mode="json") for k, s in scores.items()},
            },
        )
        write(bundle / "metrics.json", {"trials": trials})
        write(bundle / "selection.json", selected.model_dump(mode="json"))
        result = {
            **{
                k: trials[0][k]
                for k in (
                    "rows",
                    "eligible_rows",
                    "keys_sha256",
                    "eligible_keys_sha256",
                    "role_population_sha256",
                    "baseline_predictions_sha256",
                )
            },
            "selection": selected.model_dump(mode="json"),
            "family_screening": ranking.model_dump(mode="json"),
            "worker_peak_rss_bytes": 12345,
            "trial_count": 2,
            "full_tune_label_passes": 2,
            "calibration_label_passes": 0,
            "independent_or_final_label_passes": 0,
        }
        if fault == "missing-ranking":
            result.pop("family_screening")
        elif fault == "forged-ranking":
            result["family_screening"]["rows"] += 1
        elif fault == "removed-original":
            remove_original((root, protocol.screening, case[2]))
        write(folder / "select.json", result)
        return {"status": "passed", "reason": None, "sampled_tree_peak_rss_bytes": 12345}

    monkeypatch.setattr(tuner, "monitor", monitor)
    return calls


def run_selection(case):
    root, protocol, output, dataset, exports, _, scores, bundles = case
    return tuner.select_campaign_forecast(
        dataset,
        bundles,
        Path("/unused-controlled-python"),
        output,
        journal=root,
        operation_id=protocol.operations[-1].operation_id,
        plan=protocol.selection_plan,
        exported=exports[0],
        scores=scores,
    )


def test_complete_controller_retains_not_ready_and_rechecks_every_original_receipt(
    tmp_path, monkeypatch
):
    case = controlled_parents(tmp_path, monkeypatch)
    calls = controlled_selection_worker(monkeypatch, case)
    bundle, receipt = run_selection(case)
    root, protocol = case[:2]
    ranking = tuner.verified_screening_family_ranking(bundle, journal=root, receipt=receipt)
    assert calls == ["selection-worker"]
    assert ranking.status == "not_ready" and len(ranking.trials) == 6
    assert not ranking.advancement_authorized
    assert journal.summary(root)["charged_new_attempts"] == 12
    assert journal.summary(root)["charged_new_fit_attempts"] == 6
    event = journal.inspect(root).events[-1]
    assert event.result == "completed" and event.evidence_sha256 == receipt.content_sha256()
    assert event.cost.wall_seconds > 0 and event.cost.peak_process_tree_rss_bytes == 12345
    assert event.cost.artifact_bytes == receipt.artifact_bytes
    original = protocol.screening.parents.preparation
    paths = [
        Path(original.bootstrap.journal_path)
        / "receipts"
        / (original.generated.reservation_id + ".json"),
        root / "receipts" / (case[4][0].reservation_id + ".json"),
        root / "receipts" / (next(iter(case[5].values())).reservation_id + ".json"),
        root / "receipts" / (next(iter(case[6].values())).reservation_id + ".json"),
    ]
    before = journal.inspect(root)
    for path in paths:
        raw = path.read_bytes()
        path.unlink()
        try:
            with pytest.raises((OSError, SnapshotError)):
                tuner.verified_screening_family_ranking(bundle, journal=root, receipt=receipt)
        finally:
            path.write_bytes(raw)
            path.chmod(0o600)
        assert journal.inspect(root) == before
    assert tuner.verified_screening_family_ranking(bundle, journal=root, receipt=receipt) == ranking
    with pytest.raises(ValidationError, match="budget_exhausted"):
        run_selection(case)


@pytest.mark.parametrize("fault", ["missing-ranking", "forged-ranking", "removed-original"])
def test_failed_new_selection_keeps_all_costs_and_cannot_publish_completion(
    tmp_path, monkeypatch, fault
):
    case = controlled_parents(tmp_path, monkeypatch)
    controlled_selection_worker(monkeypatch, case, fault)
    with pytest.raises((SnapshotError, ValidationError, OSError)):
        run_selection(case)
    root = case[0]
    event = journal.inspect(root).events[-1]
    assert event.result == "failed" and event.cost.wall_seconds > 0
    assert event.cost.peak_process_tree_rss_bytes == 12345
    assert not (root / "receipts" / (event.reservation_id + ".json")).exists()
    assert journal.summary(root)["charged_new_attempts"] == 12
    assert journal.summary(root)["charged_new_fit_attempts"] == 6
