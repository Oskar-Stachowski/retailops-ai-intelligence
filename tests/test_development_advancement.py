"""Real journals/controllers, declared Source and model doubles; no Project evidence."""

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_campaign_fit import fake_phases as fit_phases
from test_campaign_generation import fake_phases as generation_phases
from test_campaign_score_worker import fake_fits
from test_campaign_tune_data import diagnostic, prediction
from test_development_screening import export, native_double
from test_development_screening_selection import forecasts
from test_development_screening_selection_journal import controlled_parents, run_selection
from test_development_variants import prepared_case, replay_worker, reuse, run_variant

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_export as exporter
from retailops_ai.evaluation_campaign import campaign_fit as fitter
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import campaign_score as scorer
from retailops_ai.evaluation_campaign import campaign_tune as tuner
from retailops_ai.evaluation_campaign import development_advancement as runner
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_fit import validate_completed_fit
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import parse_campaign_protocol
from retailops_ai.evaluation_campaign.campaign_score_contract import FAMILIES
from retailops_ai.evaluation_campaign.campaign_score_metrics import RawMetrics
from retailops_ai.evaluation_campaign.campaign_tune_data import choose
from retailops_ai.evaluation_campaign.development_advancement_contract import (
    ForecastScaleBudget,
    MultiscaleSelectionJournal,
    MultiscaleSelectionProtocol,
    PreparedForecastAdvancementJournal,
    PreparedForecastAdvancementProtocol,
)
from retailops_ai.evaluation_campaign.development_screening import (
    capture_prepared_development_parents,
)
from retailops_ai.evaluation_campaign.development_screening_contract import screening_operations
from retailops_ai.evaluation_campaign.development_screening_selection import rank_screening_families
from retailops_ai.evaluation_campaign.development_screening_selection_contract import (
    freeze_screening_selection,
)
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.pipeline import environment_lock


def trial_values(operation):
    return forecasts(10.1, 10.2, 10.9) if operation.endswith("2") else forecasts(10.0, 10.8, 10.3)


def full_score_phases(monkeypatch, value):
    root, _, _, plan, exported, fits = value
    operation = next(
        o.operation_id
        for o in journal.inspect(root).protocol.operations
        if o.execution_recipe_sha256 == plan.content_sha256()
    )
    rows = [prediction(i, trial_values(operation)) for i in range(100)]
    keys = hashlib.sha256(b"".join(membership_key(row) + b"\n" for row in rows)).hexdigest()
    population = dict(
        rows=100,
        eligible_rows=100,
        keys_sha256=keys,
        eligible_keys_sha256=keys,
        role_population_sha256="2" * 64,
    )

    def monitor(command, **kwargs):
        phase, folder = command[-2], kwargs["root"]
        assert journal.inspect(root).events[-1].kind == "reserved"
        if phase == "predict":
            bundle = folder / "bundle"
            bundle.mkdir(mode=0o700)
            write(bundle / "plan.json", plan.model_dump(mode="json"))
            write(
                bundle / "parents.json",
                {
                    "export_receipt_sha256": exported.content_sha256(),
                    "fit_receipt_sha256": {f: fits[f].content_sha256() for f in FAMILIES},
                    "model_artifact_sha256": {f: fits[f].model_artifact_sha256 for f in FAMILIES},
                },
            )
            raw = RawMetrics()
            for row in rows:
                raw.add(row, 10)
            write(bundle / "metrics.json", raw.result())
            (bundle / "predictions.jsonl").write_bytes(
                b"".join(canonical_bytes(row.model_dump(mode="json")) + b"\n" for row in rows)
            )
        write(
            folder / (phase + ".json"),
            {**population, "worker_peak_rss_bytes": 12345, "all_models_share_all_role_keys": True},
        )
        return dict(status="passed", reason=None, sampled_tree_peak_rss_bytes=12345)

    monkeypatch.setattr(scorer, "monitor", monitor)


def paired_selection_worker(monkeypatch, root, scores, exported):
    protocol = journal.inspect(root).protocol
    plan = protocol.selection_plan

    def monitor(command, **kwargs):
        folder = kwargs["root"]
        assert journal.inspect(root).events[-1].kind == "reserved"
        request = read(folder / "request.json")
        trials = []
        for op in plan.score_operation_ids:
            score = scores[op]
            trial, _ = diagnostic(op, forecasts=trial_values(op))
            trial.update(
                {
                    k: getattr(score, k)
                    for k in (
                        "rows",
                        "eligible_rows",
                        "keys_sha256",
                        "eligible_keys_sha256",
                        "role_population_sha256",
                    )
                }
            )
            trials.append(trial)
        selected = choose(trials, scores, plan)
        bundle = folder / "bundle"
        bundle.mkdir(mode=0o700)
        write(bundle / "plan.json", plan.model_dump(mode="json"))
        write(
            bundle / "parents.json",
            {
                "export_receipt_sha256": exported.content_sha256(),
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
            "worker_peak_rss_bytes": 12345,
            "trial_count": len(trials),
            "full_tune_label_passes": len(trials),
            "calibration_label_passes": 0,
            "independent_or_final_label_passes": 0,
        }
        if request.get("family_screening"):
            result["family_screening"] = rank_screening_families(trials, scores, plan).model_dump(
                mode="json"
            )
        write(folder / "select.json", result)
        return dict(status="passed", reason=None, sampled_tree_peak_rss_bytes=12345)

    monkeypatch.setattr(tuner, "monitor", monitor)


@pytest.fixture(scope="session")
def completed(completed_forecast_advancement):
    return completed_forecast_advancement


def build_completed_advancement(tmp_path_factory):
    root = tmp_path_factory.mktemp("controlled-advancement").resolve()
    with pytest.MonkeyPatch.context() as monkeypatch:

        def protocol_factory(prefix):
            lock = hashlib.sha256(environment_lock()).hexdigest()
            fits = tuple(
                f.model_copy(update={"worker_environment_lock_sha256": lock}) for f in prefix.fits
            )
            scores = tuple(
                s.model_copy(update={"worker_environment_lock_sha256": lock}) for s in prefix.scores
            )
            prefix = prefix.model_copy(
                update={
                    "fits": fits,
                    "scores": scores,
                    "operations": screening_operations(
                        prefix.sources, prefix.exports, fits, scores
                    ),
                }
            )

            def budget(products):
                path = root / str(products)
                return ForecastScaleBudget(
                    bootstrap_journal_path=str(
                        path / ("canonical-journal" if products == 100 else "journal")
                    ),
                    preparation_journal_path=str(path / "variant-journal"),
                    execution_journal_path=str(path / "execution"),
                    preparation=prefix.parents.preparation.bootstrap.generation.resources,
                    fit=prefix.fits[0].resources,
                    score=prefix.scores[0].resources,
                    maximum_total_preparation_wall_seconds=3600,
                )

            return runner.freeze_multiscale_selection(
                freeze_screening_selection(prefix), fifty=budget(50), hundred=budget(100)
            )

        initial = root / "25"
        initial.mkdir(mode=0o700)
        case25 = controlled_parents(
            initial, monkeypatch, protocol_factory=protocol_factory, score_phases=full_score_phases
        )
        paired_selection_worker(monkeypatch, case25[0], case25[6], case25[4][0])
        bundle25, selected25 = run_selection(case25)
        screening = runner.capture_completed_screening(
            bundle25, journal=case25[0], selected=selected25
        )
        fifty = root / "50"
        fifty.mkdir(mode=0o700)
        prep = prepared_case(fifty, monkeypatch, products=50)
        replay_worker(monkeypatch, prep)
        ordinary = reuse(prep)
        variants = []
        for i, name in enumerate(("demand", "physical"), start=1):
            generation_phases(
                monkeypatch,
                (
                    prep[0],
                    prep[1],
                    prep[2].operations[i].operation_id,
                    prep[2].planned.preparation.generations[i],
                ),
            )
            variants.append(run_variant(prep, name)[2])
        parents = capture_prepared_development_parents(
            prep[0], ordinary=ordinary, demand=variants[0], physical=variants[1]
        )
        protocol = runner.compile_forecast_advancement(screening=screening, parents=parents)
        result, bundle = run_winning_scale(
            monkeypatch, protocol, prep[1], runner.verify_forecast_advancement
        )
        return screening, parents, protocol, result, bundle


def run_winning_scale(monkeypatch, protocol, output, verifier):
    """Original export/RF/score/Tune controllers with explicitly controlled worker outputs."""
    execution = Path(protocol.journal_path)
    journal.initialize(execution, protocol)
    case50 = execution, protocol, output
    exports, datasets = [], []
    for i in range(3):
        native_double(monkeypatch, case50, i)
        dataset, receipt = export(case50, i)
        datasets.append(dataset)
        exports.append(receipt)
    prototype = fake_fits(exports[0].dataset_id)
    fits = {}
    for operation, fit in zip(protocol.operations[3:6], protocol.fits, strict=True):
        if fit.family == "rf":
            phases = fit_phases(
                monkeypatch,
                (execution, datasets[0], output, fit, exports[0]),
                operation=operation.operation_id,
            )
            _, receipt = fitter.fit_campaign_forecast(
                datasets[0],
                Path("/controlled-python"),
                output,
                journal=execution,
                operation_id=operation.operation_id,
                plan=fit,
                exported=exports[0],
            )
            assert phases == ["prepare", "fit", "reload"]
            fits[fit.family] = receipt
            continue
        start = journal.reserve(execution, operation.operation_id)
        receipt = prototype[fit.family].model_copy(
            update={
                "operation_id": operation.operation_id,
                "reservation_id": str(start.reservation_id),
                "plan": fit,
                "protocol_sha256": protocol.content_sha256(),
                "runtime_code_sha256": protocol.runtime.code_sha256,
                "export_receipt_sha256": exports[0].content_sha256(),
                "dataset_id": exports[0].dataset_id,
                # These declared doubles use the RF controller's controlled
                # training population; unrelated helper defaults differ.
                **fits["rf"].model_dump(
                    include={
                        "train_keys_sha256",
                        "early_stopping_keys_sha256",
                        "train_eligible_rows",
                        "early_stopping_eligible_rows",
                    }
                ),
            }
        )
        exporter._store_receipt(execution, receipt)
        journal.finish(
            execution,
            str(start.reservation_id),
            result="completed",
            evidence_sha256=receipt.content_sha256(),
            cost=CampaignCost(
                wall_seconds=0.01,
                peak_process_tree_rss_bytes=12345,
                artifact_bytes=receipt.model_artifact_bytes,
            ),
        )
        fits[fit.family] = receipt
    monkeypatch.setattr(
        scorer,
        "verify_campaign_forecast_bundle",
        lambda bundle, *, journal, receipt: validate_completed_fit(journal, receipt),
    )
    full_score_phases(
        monkeypatch, (execution, datasets[0], output, protocol.score, exports[0], fits)
    )
    scored_bundle, scored = scorer.score_campaign_forecast(
        datasets[0],
        {f: output / ("declared-" + f) for f in FAMILIES},
        Path("/controlled-python"),
        output,
        journal=execution,
        operation_id=protocol.operations[6].operation_id,
        plan=protocol.score,
        exported=exports[0],
        fits=fits,
    )
    scores = {scored.operation_id: scored}
    paired_selection_worker(monkeypatch, execution, scores, exports[0])
    bundle, selected = tuner.select_campaign_forecast(
        datasets[0],
        {scored.operation_id: scored_bundle},
        Path("/controlled-python"),
        output,
        journal=execution,
        operation_id=protocol.operations[-1].operation_id,
        plan=protocol.selection_plan,
        exported=exports[0],
        scores=scores,
    )
    result = verifier(bundle, journal=execution, selected=selected)
    return result, bundle


def test_full_original_configurations_and_every_cost_survive_actual_journals(completed):
    screening, parents, protocol, result, _ = completed
    assert isinstance(
        journal.inspect(Path(screening.protocol.journal_path)), MultiscaleSelectionJournal
    )
    assert isinstance(
        journal.inspect(Path(protocol.journal_path)), PreparedForecastAdvancementJournal
    )
    assert parse_campaign_protocol(protocol.model_dump_json().encode()) == protocol
    assert result.status == "original_families_verified"
    assert not result.canonical_execution_authorized and not result.stage_ready
    assert len(screening.operation_costs) == 12 and len(result.operation_costs) == 8
    assert (
        result.cold_wall_seconds()
        == screening.cold_wall_seconds()
        + parents.cold_wall_seconds()
        + sum(c.wall_seconds for c in result.operation_costs)
    )
    original = {
        o.operation_id: p
        for o, p in zip(
            screening.protocol.screening.operations[3:9],
            screening.protocol.screening.fits,
            strict=True,
        )
    }
    for fit in protocol.fits:
        winner = screening.ranking.winners[fit.family]
        assert fit.model_dump(
            exclude={"source_recipe_sha256", "export_operation_id", "resources"}
        ) == original[winner.fit_operation_id].model_dump(
            exclude={"source_recipe_sha256", "export_operation_id", "resources"}
        )
    assert screening.ranking.winners["hgb"].frozen_trial_index == 1
    assert journal.summary(Path(protocol.journal_path))["charged_new_fit_attempts"] == 3
    with pytest.raises(SnapshotError, match="path_already_used"):
        runner.compile_forecast_advancement(screening=screening, parents=parents)


@pytest.mark.parametrize(
    "change", ["config", "seed", "resources", "path", "parent_path", "coverage", "budget"]
)
def test_resealed_plan_cannot_change_original_winner_policy_or_budget(completed, change):
    protocol = completed[2]
    doc = protocol.model_dump(mode="json")
    if change == "config":
        doc["fits"][0]["rf"]["max_depth"] += 1
    elif change == "seed":
        doc["fits"][0]["initialization_seed"] = 137
    elif change == "resources":
        doc["fits"][0]["resources"]["wall_seconds"] += 1
    elif change == "path":
        doc["journal_path"] += "-retry"
    elif change == "parent_path":
        doc["parents"]["preparation"]["journal_path"] += "-retry"
    elif change == "coverage":
        doc["exports"][0]["max_population_rows"] -= 1
    else:
        doc["maximum_new_fit_attempts"] = 4
    with pytest.raises(ValidationError):
        PreparedForecastAdvancementProtocol.model_validate_json(canonical_bytes(doc))


@pytest.mark.parametrize("origin", ["screening_fit", "fifty_generation"])
def test_missing_original_receipt_blocks_reverification_without_new_attempt(completed, origin):
    screening, parents, protocol, result, bundle = completed
    if origin == "screening_fit":
        ledger = journal.inspect(Path(screening.protocol.journal_path))
        event = next(
            e
            for e in ledger.events
            if e.kind == "finished"
            and e.operation_id == screening.protocol.operations[3].operation_id
        )
        path = Path(screening.protocol.journal_path) / "receipts" / (event.reservation_id + ".json")
    else:
        path = (
            Path(parents.preparation.journal_path)
            / "receipts"
            / (parents.variant_generations[0].reservation_id + ".json")
        )
    raw = path.read_bytes()
    before = journal.inspect(Path(protocol.journal_path))
    path.unlink()
    try:
        with pytest.raises((OSError, SnapshotError)):
            runner.verify_forecast_advancement(
                bundle, journal=Path(protocol.journal_path), selected=result.selected
            )
    finally:
        path.write_bytes(raw)
        path.chmod(0o600)
    assert journal.inspect(Path(protocol.journal_path)) == before


def test_budgets_cannot_be_attached_after_the_screening_journal_exists(completed):
    original = completed[0].protocol
    doc = original.model_dump(mode="json")
    doc.pop("multiscale_selection_version")
    doc.pop("future_budgets")
    doc.pop("future_data_rule")
    from retailops_ai.evaluation_campaign.development_screening_selection_contract import (
        PreparedDevelopmentSelectionProtocol,
    )

    prefix = PreparedDevelopmentSelectionProtocol.model_validate_json(canonical_bytes(doc))
    with pytest.raises(SnapshotError, match="before_execution"):
        runner.freeze_multiscale_selection(
            prefix, fifty=original.future_budgets["50"], hundred=original.future_budgets["100"]
        )
    changed = original.model_dump(mode="json")
    changed["future_budgets"]["100"]["execution_journal_path"] = original.future_budgets[
        "50"
    ].execution_journal_path
    with pytest.raises(ValidationError, match="journals"):
        MultiscaleSelectionProtocol.model_validate_json(canonical_bytes(changed))


def test_incompatible_fit_and_scoring_environments_rejected_before_first_trial(completed):
    prefix = completed[0].protocol.screening
    fits = tuple(
        f.model_copy(update={"worker_environment_lock_sha256": "f" * 64}) if f.family == "rf" else f
        for f in prefix.fits
    )
    changed = prefix.model_copy(
        update={
            "fits": fits,
            "operations": screening_operations(prefix.sources, prefix.exports, fits, prefix.scores),
        }
    )
    with pytest.raises(ValidationError, match="common_worker_environment"):
        type(prefix).model_validate_json(changed.model_dump_json())
