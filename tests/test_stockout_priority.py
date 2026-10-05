"""Global origin capacity cannot be reset by projection or applied to current stockouts."""

import pytest
from test_stockout_batch import backend as backend
from test_stockout_batch import conditional as conditional
from test_stockout_batch import context as context
from test_stockout_batch import job as job
from test_stockout_batch import records as records
from test_stockout_batch import source as source
from test_stockout_lifecycle import sealed

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal, StockoutAccess
from retailops_ai.stockout_jobs.batch import StockoutOutput, compute
from retailops_ai.stockout_jobs.priority import priorities
from retailops_ai.stockout_jobs.read_contracts import StockoutQuery
from retailops_ai.stockout_runtime.inputs import PreparedStockoutInputs


@pytest.fixture
def universe(job):
    run, inputs, release, now = job
    point = inputs.points[0].model_dump(mode="json")
    points = []
    products = ["product" + str(i) for i in range(10)]
    for product in products:
        import copy

        value = copy.deepcopy(point)
        value["feature"]["product_id"] = product
        value["upstream"]["product_id"] = product
        points.append(value)
    body = inputs.model_dump(mode="json", exclude={"inputs_id"})
    body["scope"]["product_ids"] = products
    body["points"] = points
    inputs = sealed(PreparedStockoutInputs, "inputs_id", "stockout-inputs-sha256-", body)
    output = compute(run, job[1], release, generated_at=now)
    item = output.items[0].model_dump(mode="json")
    rows = []
    for i, product in enumerate(products):
        row = dict(
            item,
            product_id=product,
            probability=float(i / 10),
            risk_band="low" if i < 3 else "medium" if i < 5 else "high" if i < 9 else "critical",
        )
        row["risk_id"] = "risk-sha256-" + canonical_sha256(
            [product, row["stock_location_id"], row["as_of"], row["inference_run_id"]]
        )
        rows.append(row)
    body = output.model_dump(mode="json", exclude={"output_id"})
    body.update(profile_id=inputs.inputs_id, items=rows)
    output = sealed(StockoutOutput, "output_id", "stockout-output-sha256-", body)
    return output, inputs


def actor(inputs, products=None):
    return Principal(
        "mechanics-reader",
        frozenset({"analyst"}),
        frozenset({"stockout:read"}),
        frozenset(),
        frozenset(),
        frozenset(),
        stockout=StockoutAccess(
            frozenset(products if products is not None else inputs.scope.product_ids),
            frozenset(inputs.scope.stock_location_ids),
        ),
    )


def test_complete_origin_queue_is_ranked_once_before_product_projection(universe):
    output, inputs = universe
    rows = priorities(output, inputs, actor(inputs))
    assert sum(p.selected_at_origin is True for p in rows.values()) == 1
    assert rows[output.items[-1].risk_id].rank_at_origin == 1
    assert rows[output.items[0].risk_id].rank_at_origin == 10
    assert rows[output.items[0].risk_id].selected_at_origin is False
    assert {p.capacity_slots for p in rows.values()} == {1}
    assert {p.universe_id for p in rows.values()} == {inputs.inputs_id}


def test_restricted_scope_keeps_same_selection_and_withholds_unseen_counts_and_ranks(universe):
    output, inputs = universe
    rows = priorities(output, inputs, actor(inputs, [output.items[0].product_id]))
    visible = rows[output.items[0].risk_id]
    assert visible.selected_at_origin is False
    assert visible.rank_at_origin is None
    assert visible.eligible_in_universe is None and visible.capacity_slots is None
    assert visible.other_scope_context == "withheld"


def test_current_stockout_never_consumes_incident_risk_capacity(universe):
    output, inputs = universe
    body = output.model_dump(mode="json", exclude={"output_id"})
    body["items"][-1].update(
        status="already_stockout",
        status_reason="already_stockout",
        probability=None,
        risk_band=None,
    )
    changed = sealed(StockoutOutput, "output_id", "stockout-output-sha256-", body)
    rows = priorities(changed, inputs, actor(inputs))
    current = rows[changed.items[-1].risk_id]
    assert current.selected_at_origin is None and current.rank_at_origin is None
    assert current.eligible_in_universe == 9
    assert rows[changed.items[-2].risk_id].selected_at_origin is True


def test_incomplete_subset_cannot_claim_a_new_capacity_universe(universe):
    output, inputs = universe
    body = output.model_dump(mode="json", exclude={"output_id"})
    body["items"] = body["items"][:1]
    changed = sealed(StockoutOutput, "output_id", "stockout-output-sha256-", body)
    with pytest.raises(ValueError, match="complete_profile"):
        priorities(changed, inputs, actor(inputs))


def test_attention_view_requires_one_complete_run_and_separates_current_stockouts():
    with pytest.raises(ValueError, match="complete_inference_run"):
        StockoutQuery(view="attention_queue")
    assert (
        StockoutQuery(view="attention_queue", inference_run_id="run-" + "0" * 32).view
        == "attention_queue"
    )
    assert StockoutQuery(view="current_stockouts").view == "current_stockouts"
