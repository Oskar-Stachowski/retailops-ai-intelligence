"""Actual byte receipts must preserve failed quality and cannot forge successful gates."""

from copy import deepcopy

import pytest
from test_stockout_final_campaign import frozen as frozen
from test_stockout_final_campaign import recipes as recipes

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.stockout_campaign.assembly import FinalData
from retailops_ai.stockout_campaign.evaluation import evaluate_final
from retailops_ai.stockout_lifecycle import evidence
from retailops_ai.stockout_lifecycle.release import receipt


@pytest.fixture
def receipts(frozen, recipes, tmp_path):
    freeze, permission = frozen
    recipe, policy = recipes
    rows = [
        dict(
            product_id="fixture-" + str(i),
            stock_location_id=recipe.pipeline.calibrator.stock_locations[0],
            category_id=recipe.pipeline.calibrator.categories[0],
            as_of="2026-07-25T23:59:59Z",
            values={
                **{k: None for k in recipe.pipeline.base.preprocessing.numeric_columns},
                "history_constrained_days": i % 2,
                "available_qty": 10,
                "forecast_unavailable": 0,
            },
        )
        for i in range(4)
    ]
    data = FinalData(
        rows,
        [0, 1, 0, 1],
        dict(eligible=4),
        "1" * 64,
        "2" * 64,
        {k: [] for k in ("promotion_plans", "fulfillment_routes", "assortment")},
    )
    roots = {}
    for source in freeze.sources:
        root = tmp_path / f"{source.world}-{source.seed}"
        root.mkdir()
        roots[source.world, source.seed] = root
        report = evaluate_final(
            data, freeze=freeze, permission=permission, source=source, recipe=recipe, policy=policy
        )
        raw = {name: canonical_bytes(report) + b"\n" for name in ("native.json", "wheel.json")}
        for phase in ("native", "wheel"):
            events = [
                dict(
                    at=f"2026-10-05T00:00:0{i + 1}Z",
                    campaign_id=freeze.campaign_id,
                    source_dataset_id=source.source_dataset_id,
                    world=source.world,
                    seed=source.seed,
                    permission_sha256=canonical_sha256(permission.model_dump(mode="json")),
                    approved_by=permission.approved_by,
                    status=status,
                    model_refits=0,
                )
                for i, status in enumerate(("authorized_before_private_read", "complete_not_ready"))
            ]
            raw[phase + "-access.jsonl"] = b"".join(canonical_bytes(e) + b"\n" for e in events)
        resource = dict(
            schema_version="stockout-final-world-resource-1.0.0",
            status="passed",
            campaign_id=freeze.campaign_id,
            world=source.world,
            seed=source.seed,
            budgets=evidence.LIMITS,
            execution_code_sha256=freeze.evaluator_code_sha256,
            dependency_lock_sha256=freeze.dependency_lock_sha256,
            execution_commit="a" * 40,
            workflow_run_id=1,
            workflow_run_attempt=1,
            phases=[dict(phase=p, exit_code=0, wall_seconds=1.0) for p in ("native", "wheel")],
            native_wheel_equal=True,
            receipts={name: receipt(value).model_dump(mode="json") for name, value in raw.items()},
            recorded_at="2026-10-05T00:00:03Z",
            failure=None,
            measurement=dict(
                wall_seconds=2.0,
                sampled_tree_peak_rss_bytes=1000,
                sampled_peak_scratch_logical_bytes=1000,
                sampled_peak_scratch_allocated_bytes=1000,
                minimum_free_bytes=20 * 1024**3,
                samples=10,
            ),
            model_refits=0,
            model_promoted=False,
            ai08_ready=False,
        )
        raw["resource.json"] = canonical_bytes(resource) + b"\n"
        for name, value in raw.items():
            (root / name).write_bytes(value)
    return roots


def test_six_complete_cold_receipts_do_not_turn_failed_quality_into_ready(frozen, receipts):
    freeze, permission = frozen
    result = evidence.collect(receipts, freeze=freeze, permission=permission)
    assert result["final_quality"]["content"]["status"] == "not_ready"
    assert len(result["final_quality"]["content"]["blockers"]) == 6
    assert result["execution_evidence"]["content"]["native_wheel_equal"] is True
    assert result["execution_evidence"]["content"]["ai08_ready"] is False
    evidence.verify_execution_evidence(
        result["execution_evidence"], result["final_quality"], freeze=freeze, permission=permission
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "changed_bytes",
        "unequal",
        "no_equality",
        "over_budget",
        "wrong_run",
        "audit",
        "forged_gates",
    ],
)
def test_incomplete_mixed_or_forged_receipts_fail_closed(frozen, receipts, mutation):
    import json

    freeze, permission = frozen
    roots = dict(receipts)
    root = next(iter(roots.values()))
    if mutation == "missing":
        roots.pop(next(iter(roots)))
    elif mutation == "changed_bytes":
        (root / "native.json").write_bytes(b"{}")
    elif mutation in {"no_equality", "over_budget", "wrong_run"}:
        r = json.loads((root / "resource.json").read_bytes())
        if mutation == "no_equality":
            r["native_wheel_equal"] = False
        elif mutation == "over_budget":
            r["measurement"]["sampled_tree_peak_rss_bytes"] = evidence.LIMITS["tree_rss_bytes"] + 1
        else:
            r["workflow_run_id"] = 2
        (root / "resource.json").write_bytes(canonical_bytes(r) + b"\n")
    else:
        r = json.loads((root / "resource.json").read_bytes())
        if mutation == "audit":
            name = "native-access.jsonl"
            raw = (root / name).read_bytes().splitlines()[1] + b"\n"
            (root / name).write_bytes(raw)
            r["receipts"][name] = receipt(raw).model_dump(mode="json")
        else:
            report = json.loads((root / "native.json").read_bytes())
            report = deepcopy(report)
            report["segment_gates"]["segments"]["all"]["status"] = "passed"
            for name in (
                ("native.json", "wheel.json") if mutation == "forged_gates" else ("wheel.json",)
            ):
                raw = canonical_bytes(report) + b"\n"
                (root / name).write_bytes(raw)
                r["receipts"][name] = receipt(raw).model_dump(mode="json")
        (root / "resource.json").write_bytes(canonical_bytes(r) + b"\n")
    with pytest.raises(ValueError):
        evidence.collect(roots, freeze=freeze, permission=permission)


def test_permission_is_checked_before_reading_any_receipt(frozen, receipts, monkeypatch):
    freeze, _ = frozen
    monkeypatch.setattr(evidence, "read_bytes", lambda *a, **k: pytest.fail("no unauthorized read"))
    with pytest.raises(ValueError, match="permission_required"):
        evidence.collect(receipts, freeze=freeze, permission=None)


def test_real_collection_cli_accepts_exact_execution_and_keeps_output_immutable(
    frozen, receipts, tmp_path
):
    import json
    import subprocess
    import sys
    from pathlib import Path

    freeze, permission = frozen
    freeze_path, permission_path = tmp_path / "freeze.json", tmp_path / "permission.json"
    freeze_path.write_bytes(canonical_bytes(freeze.model_dump(mode="json")))
    permission_path.write_bytes(canonical_bytes(permission.model_dump(mode="json")))
    prefix = "ai08-final-" + "a" * 40 + "-"
    for (world, seed), root in receipts.items():
        root.rename(root.with_name(prefix + f"{world}-{seed}"))
    output = tmp_path / "collected"
    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts/collect_stockout_final.py"),
        "--freeze",
        str(freeze_path),
        "--permission",
        str(permission_path),
        "--receipts",
        str(tmp_path),
        "--output",
        str(output),
        "--artifact-prefix",
        prefix,
        "--workflow-run-id",
        "1",
    ]
    first = subprocess.run(command, capture_output=True, check=True, timeout=30)
    assert json.loads(first.stdout)["status"] == "not_ready"
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    assert set(before) == {"final_quality.json", "execution_evidence.json"}
    assert output.stat().st_mode & 0o077 == 0
    again = subprocess.run(command, capture_output=True, check=False, timeout=30)
    assert again.returncode == 1
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}
