"""Actual journal/receipt transfer with controlled workers, not Project fit evidence."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_campaign_generation import fake_phases
from test_development_variants import prepared_case, replay_worker, reuse, run_variant

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_export as exporter
from retailops_ai.evaluation_campaign import campaign_fit as fitter
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignDevelopmentExportPlan
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitPlan
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScorePlan
from retailops_ai.evaluation_campaign.development_screening import (
    capture_prepared_development_parents,
    compile_prepared_development_screening,
    verify_prepared_development_parents,
)
from retailops_ai.evaluation_campaign.development_screening_contract import (
    PreparedDevelopmentScreeningJournal,
    PreparedDevelopmentScreeningProtocol,
    screening_operations,
)
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.forecasting.model_contract import HGBConfig, RFConfig
from retailops_ai.source_snapshot.files import SnapshotError


def setup(tmp_path, monkeypatch):
    case = prepared_case(tmp_path, monkeypatch)
    root, output, p, _, _ = case
    replay_worker(monkeypatch, case)
    ordinary = reuse(case)
    generated = []
    for i, name in enumerate(("demand", "physical"), start=1):
        fake_phases(
            monkeypatch,
            (root, output, p.operations[i].operation_id, p.planned.preparation.generations[i]),
        )
        generated.append(run_variant(case, name)[2])
    parents = capture_prepared_development_parents(
        root, ordinary=ordinary, demand=generated[0], physical=generated[1]
    )
    roles = p.bootstrap.planning.partitions.roles
    exports = tuple(
        CampaignDevelopmentExportPlan(
            source_recipe_sha256=s.content_sha256(),
            generation_operation_id=g.operation_id,
            origins=OriginWindow(start=roles[0].origins.start, end=roles[-1].origins.end),
            roles=roles,
        )
        for s, g in zip(p.sources, parents.generations, strict=True)
    )
    resources = p.reuse.resources.model_copy(update={"wall_seconds": 1200})
    fits = tuple(
        CampaignForecastFitPlan(
            source_recipe_sha256=p.sources[0].content_sha256(),
            export_operation_id="development-25-ordinary-export",
            family=family,
            initialization_seed=42,
            worker_environment_lock_sha256="a" * 64,
            rf=RFConfig(max_depth=10 + i),
            hgb=HGBConfig(max_iter=80 + i),
            learning_rate=0.001 * (i + 1),
            resources=resources,
        )
        for family in ("rf", "hgb", "tensorflow")
        for i in (0, 1)
    )
    # Unused configuration fields must also stay fixed within a family.
    fits = tuple(
        f.model_copy(
            update={
                **({"hgb": HGBConfig(), "learning_rate": 0.001} if f.family == "rf" else {}),
                **({"rf": RFConfig(), "learning_rate": 0.001} if f.family == "hgb" else {}),
                **({"rf": RFConfig(), "hgb": HGBConfig()} if f.family == "tensorflow" else {}),
            }
        )
        for f in fits
    )
    scores = tuple(
        CampaignForecastScorePlan(
            source_recipe_sha256=p.sources[0].content_sha256(),
            export_operation_id="development-25-ordinary-export",
            fit_operation_ids={
                f: f"development-25-{f}-trial-{i}" for f in ("rf", "hgb", "tensorflow")
            },
            role="tune",
            worker_environment_lock_sha256="a" * 64,
            resources=resources,
        )
        for i in (1, 2)
    )
    path = tmp_path / "screening"
    protocol = compile_prepared_development_screening(
        path, parents=parents, exports=exports, fits=fits, scores=scores
    )
    journal.initialize(path, protocol)
    return path, protocol, output


def native_double(monkeypatch, case, position=0, mutate_on_exit=None):
    root, p, _ = case
    source = p.sources[position]
    gen = p.parents.generations[position]
    plan = p.exports[position]
    operation = p.operations[position].operation_id
    calls = []

    def check():
        assert journal.inspect(root).events[-1].operation_id == operation
        assert journal.inspect(root).events[-1].kind == "reserved"
        calls.append("reserved")

    replay = SimpleNamespace(
        snapshot_inventory_sha256="a" * 64,
        curated_inventory_sha256="b" * 64,
        logical_curated_sha256="c" * 64,
        producer_commit=source.producer_commit,
        producer_code_state="clean",
        producer_lock_sha256=source.producer_lock_sha256,
        exporter_commit=source.producer_commit,
        exporter_lock_sha256=source.exporter_lock_sha256,
        declared_exporter_lock_sha256=source.exporter_lock_sha256,
        check_parents=check,
    )

    @contextmanager
    def opened(*args, **kwargs):
        check()
        yield replay
        check()
        if mutate_on_exit:
            mutate_on_exit()

    def build(parent, recipe, output):
        check()
        assert parent is replay and recipe == plan.bind(gen.source)
        path = output / ("ai09-physical-forecast-sha256-" + str(position) * 64)
        path.mkdir()
        (path / "manifest.json").write_bytes(canonical_bytes({"controlled": True}))
        return path

    def verify(path):
        check()
        return SimpleNamespace(
            dataset_id=path.name,
            descriptor=SimpleNamespace(
                recipe=plan.bind(gen.source),
                runtime=p.runtime,
                snapshot_inventory_sha256=replay.snapshot_inventory_sha256,
                curated_inventory_sha256=replay.curated_inventory_sha256,
                logical_curated_sha256=replay.logical_curated_sha256,
                feature_descriptor=SimpleNamespace(row_count=75),
            ),
        )

    monkeypatch.setattr(exporter, "_open_verified_source_parent", opened)
    monkeypatch.setattr(exporter, "_build_physical_forecast", build)
    monkeypatch.setattr(exporter, "verify_physical_forecast", verify)
    return calls


def export(case, position=0):
    root, p, output = case
    return exporter.export_development_forecast(
        output / "unused-snapshot",
        output / "unused-curated",
        output,
        journal=root,
        operation_id=p.operations[position].operation_id,
        plan=p.exports[position],
        generated=p.parents.generations[position],
    )


def test_all_three_original_parents_export_without_forged_generation_or_free_cost(
    tmp_path, monkeypatch
):
    case = setup(tmp_path, monkeypatch)
    root, p, _ = case
    assert isinstance(journal.inspect(root), PreparedDevelopmentScreeningJournal)
    assert not any(o.action == "source_generate" for o in p.operations)
    assert p.maximum_new_fit_attempts == 6
    assert p.parents.cold_wall_seconds() == p.parents.preparation.cold_wall_seconds() + sum(
        c.wall_seconds for c in p.parents.preparation_operation_costs
    )
    for i in range(3):
        calls = native_double(monkeypatch, case, i)
        _, receipt = export(case, i)
        exporter.validate_completed_export(root, receipt)
        assert (
            calls
            and receipt.generated_parent_receipt_sha256 == p.parents.generations[i].content_sha256()
        )
        assert receipt.protocol_sha256 != p.parents.generations[i].protocol_sha256
    assert journal.summary(root)["charged_new_attempts"] == 3
    assert journal.summary(root)["charged_new_fit_attempts"] == 0
    before = journal.inspect(root)
    verify_prepared_development_parents(p.parents)
    assert journal.inspect(root) == before
    with pytest.raises(ValidationError, match="budget_exhausted"):
        export(case)


def remove_original(case):
    p = case[1].parents.preparation

    (Path(p.bootstrap.journal_path) / "receipts" / (p.generated.reservation_id + ".json")).unlink()


@pytest.mark.parametrize("missing_original", [False, True])
def test_existing_fit_runner_consumes_export_with_original_ancestry_rechecked(
    tmp_path, monkeypatch, missing_original
):
    case = setup(tmp_path, monkeypatch)
    root, p, output = case
    native_double(monkeypatch, case)
    dataset, exported = export(case)
    if missing_original:
        remove_original(case)
    calls = []

    def refused_worker(command, **kwargs):
        assert journal.inspect(root).events[-1].kind == "reserved"
        assert command[-2] == "prepare"
        calls.append(command)
        return {
            "status": "failed",
            "reason": "controlled_admission_refused",
            "sampled_tree_peak_rss_bytes": None,
        }

    monkeypatch.setattr(fitter, "monitor", refused_worker)
    with pytest.raises((SnapshotError, FileNotFoundError)):
        fitter.fit_campaign_forecast(
            dataset,
            Path("/unused-worker"),
            output,
            journal=root,
            operation_id=p.operations[3].operation_id,
            plan=p.fits[0],
            exported=exported,
        )
    assert bool(calls) is not missing_original
    event = journal.inspect(root).events[-1]
    assert event.operation_id == p.operations[3].operation_id and event.result == "failed"
    assert event.cost.wall_seconds > 0 and event.cost.peak_process_tree_rss_bytes is None
    assert journal.summary(root)["charged_new_fit_attempts"] == 1


def test_missing_original_receipt_is_charged_before_any_source_io(tmp_path, monkeypatch):
    case = setup(tmp_path, monkeypatch)
    calls = native_double(monkeypatch, case)
    remove_original(case)
    with pytest.raises((FileNotFoundError, SnapshotError)):
        export(case)
    assert not calls
    assert journal.inspect(case[0]).events[-1].result == "failed"
    assert journal.inspect(case[0]).events[-1].cost.wall_seconds > 0


def test_original_receipt_changed_during_read_cannot_publish_completion(tmp_path, monkeypatch):
    case = setup(tmp_path, monkeypatch)
    native_double(monkeypatch, case, mutate_on_exit=lambda: remove_original(case))
    with pytest.raises((FileNotFoundError, SnapshotError)):
        export(case)
    event = journal.inspect(case[0]).events[-1]
    assert event.result == "failed"
    assert not (case[0] / "receipts" / (event.reservation_id + ".json")).exists()


def test_fit_waits_for_charged_export_and_tune_waits_for_all_three_fits(tmp_path, monkeypatch):
    root, p, _ = setup(tmp_path, monkeypatch)
    for operation in (*p.operations[3:9], *p.operations[9:]):
        with pytest.raises(ValidationError, match="prerequisites_not_completed"):
            journal.reserve(root, operation.operation_id)
    assert not journal.inspect(root).events


@pytest.mark.parametrize("field", ["cost", "head"])
def test_resealed_declaration_cannot_change_original_cost_or_journal_head(
    tmp_path, monkeypatch, field
):
    _, p, _ = setup(tmp_path, monkeypatch)
    value = p.parents.model_dump(mode="json")
    if field == "head":
        value["preparation_journal_head_sha256"] = "0" * 64
    else:
        value["preparation_operation_costs"][0]["wall_seconds"] += 1
    changed = type(p.parents).model_validate_json(canonical_bytes(value))
    with pytest.raises(SnapshotError, match="journal_or_cost_changed"):
        verify_prepared_development_parents(changed)


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate_recipe",
        "unequal_budget",
        "calibration_score",
        "wrong_family_fit",
        "changed_roles",
    ],
)
def test_frozen_scope_rejects_silent_budget_population_and_selection_changes(
    tmp_path, monkeypatch, kind
):
    _, p, _ = setup(tmp_path, monkeypatch)
    value = p.model_dump(mode="json")
    if kind == "duplicate_recipe":
        value["fits"][1]["rf"] = value["fits"][0]["rf"]
    elif kind == "unequal_budget":
        value["fits"][4]["resources"]["wall_seconds"] -= 1
    elif kind == "calibration_score":
        value["scores"][0]["role"] = "calibration"
    elif kind == "wrong_family_fit":
        value["scores"][0]["fit_operation_ids"]["rf"] = "development-25-hgb-trial-2"
    else:
        value["exports"][0]["roles"][0]["label_knowledge_cutoff"] = "2025-10-16T23:59:59.999999Z"
    # Reseal the declared operations too: semantic checks must still reject it.
    exports = tuple(
        CampaignDevelopmentExportPlan.model_validate_json(canonical_bytes(v))
        for v in value["exports"]
    )
    fits = tuple(
        CampaignForecastFitPlan.model_validate_json(canonical_bytes(v)) for v in value["fits"]
    )
    scores = tuple(
        CampaignForecastScorePlan.model_validate_json(canonical_bytes(v)) for v in value["scores"]
    )
    value["operations"] = [
        o.model_dump(mode="json") for o in screening_operations(p.sources, exports, fits, scores)
    ]
    with pytest.raises(ValidationError):
        PreparedDevelopmentScreeningProtocol.model_validate_json(canonical_bytes(value))
