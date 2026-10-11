"""Original 25/50/100 journals, with declared worker doubles rather than Project evidence."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from test_campaign_generation import fake_phases as generation_phases
from test_development_advancement import completed as completed
from test_development_advancement import run_winning_scale
from test_development_variants import prepared_case, replay_worker, reuse, run_variant

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import parse_campaign_protocol
from retailops_ai.evaluation_campaign.canonical_development import (
    capture_canonical_development_parents,
)
from retailops_ai.evaluation_campaign.canonical_forecast import (
    compile_canonical_forecast,
    verify_canonical_forecast,
    verify_completed_advancement,
)
from retailops_ai.evaluation_campaign.canonical_forecast_contract import (
    PreparedCanonicalForecastJournal,
    PreparedCanonicalForecastProtocol,
)
from retailops_ai.evaluation_campaign.development_advancement_contract import (
    CompletedForecastAdvancement,
)
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.fixture(scope="module")
def canonical(completed):
    advancement = completed[3]
    budget = advancement.protocol.screening.protocol.future_budgets["100"]
    root = Path(budget.preparation_journal_path).parent
    root.mkdir(mode=0o700)
    with pytest.MonkeyPatch.context() as monkeypatch:
        preparation = prepared_case(root, monkeypatch, products=100)
        replay_worker(monkeypatch, preparation)
        ordinary = reuse(preparation)
        variants = []
        for i, name in enumerate(("demand", "physical"), start=1):
            generation_phases(
                monkeypatch,
                (
                    preparation[0],
                    preparation[1],
                    preparation[2].operations[i].operation_id,
                    preparation[2].planned.preparation.generations[i],
                ),
            )
            variants.append(run_variant(preparation, name)[2])
        parents = capture_canonical_development_parents(
            preparation[0], ordinary=ordinary, demand=variants[0], physical=variants[1]
        )
        protocol = compile_canonical_forecast(advancement=advancement, parents=parents)
        result, bundle = run_winning_scale(
            monkeypatch, protocol, preparation[1], verify_canonical_forecast
        )
        yield protocol, result, bundle


def test_canonical_run_preserves_original_winners_and_every_scale_cost(canonical):
    protocol, result, _ = canonical
    assert isinstance(
        journal.inspect(Path(protocol.journal_path)), PreparedCanonicalForecastJournal
    )
    assert parse_campaign_protocol(protocol.model_dump_json().encode()) == protocol
    assert all(
        s.products == 100 and "development_profile" not in s.model_dump() for s in protocol.sources
    )
    assert result.status == "original_families_verified"
    assert not result.final_test_access_authorized and not result.stage_ready
    assert len(result.operation_costs) == 8
    assert result.cold_wall_seconds() == (
        protocol.advancement.cold_wall_seconds()
        + protocol.parents.cold_wall_seconds()
        + sum(c.wall_seconds for c in result.operation_costs)
    )
    for original, current in zip(protocol.advancement.protocol.fits, protocol.fits, strict=True):
        excluded = {"source_recipe_sha256", "export_operation_id", "resources"}
        assert current.model_dump(exclude=excluded) == original.model_dump(exclude=excluded)
    assert journal.summary(Path(protocol.journal_path))["charged_new_fit_attempts"] == 3
    with pytest.raises(SnapshotError, match="path_already_used"):
        compile_canonical_forecast(advancement=protocol.advancement, parents=protocol.parents)


@pytest.mark.parametrize(
    "change", ["config", "seed", "resources", "path", "parent_path", "budget", "fifty_status"]
)
def test_canonical_scope_cannot_change_original_winners_or_reset_attempts(canonical, change):
    raw = canonical[0].model_dump(mode="json")
    if change == "config":
        raw["fits"][0]["rf"]["max_depth"] += 1
    elif change == "seed":
        raw["fits"][0]["initialization_seed"] = 137
    elif change == "resources":
        raw["fits"][0]["resources"]["wall_seconds"] += 1
    elif change == "path":
        raw["journal_path"] += "-retry"
    elif change == "parent_path":
        raw["parents"]["preparation"]["journal_path"] += "-retry"
    elif change == "budget":
        raw["maximum_new_fit_attempts"] = 4
    else:
        raw["advancement"].update(
            status="not_ready",
            family_reasons={"rf": [], "hgb": [], "tensorflow": ["incomplete_mean_predictions"]},
            reasons=["invalid_tensorflow_family"],
        )
        CompletedForecastAdvancement.model_validate_json(canonical_bytes(raw["advancement"]))
    with pytest.raises(ValidationError):
        PreparedCanonicalForecastProtocol.model_validate_json(canonical_bytes(raw))


@pytest.mark.parametrize("origin", ["screening_fit", "fifty_fit", "canonical_generation"])
def test_missing_any_original_receipt_blocks_result_without_new_attempt(canonical, origin):
    protocol, result, bundle = canonical
    if origin == "canonical_generation":
        root = Path(protocol.parents.preparation.journal_path)
        receipt = protocol.parents.variant_generations[1]
        path = root / "receipts" / (receipt.reservation_id + ".json")
    else:
        previous = (
            protocol.advancement.protocol.screening.protocol
            if origin == "screening_fit"
            else protocol.advancement.protocol
        )
        root = Path(previous.journal_path)
        event = next(
            e
            for e in journal.inspect(root).events
            if e.kind == "finished" and e.operation_id == previous.operations[3].operation_id
        )
        path = root / "receipts" / (event.reservation_id + ".json")
    saved = path.read_bytes()
    before = journal.inspect(Path(protocol.journal_path))
    path.unlink()
    try:
        with pytest.raises((OSError, SnapshotError)):
            verify_canonical_forecast(
                bundle, journal=Path(protocol.journal_path), selected=result.selected
            )
    finally:
        path.write_bytes(saved)
        path.chmod(0o600)
    assert journal.inspect(Path(protocol.journal_path)) == before


def test_altered_original_fifty_cost_cannot_be_resealed_as_new_evidence(canonical):
    original = canonical[0].advancement
    changed = original.model_copy(
        update={
            "operation_costs": (
                original.operation_costs[0].model_copy(
                    update={"wall_seconds": original.operation_costs[0].wall_seconds + 1}
                ),
                *original.operation_costs[1:],
            )
        }
    )
    with pytest.raises(SnapshotError, match="original_fifty_evidence_or_cost_changed"):
        verify_completed_advancement(changed)
