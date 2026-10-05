"""Receipt/closure clocks, event/currency isolation and independently sealed feature inputs."""

import hashlib
import json
import shutil
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile

import pytest
from jsonschema import Draft202012Validator

from retailops_ai.curated.builder import build_curated
from retailops_ai.day_qualification.contract import Day
from retailops_ai.day_qualification.gate import DayGate
from retailops_ai.full_raw_dq.source import ParentFacts
from retailops_ai.full_raw_dq.store import build_replay
from retailops_ai.qualified_anomalies.contract import (
    MODEL_FEATURES,
    Context,
    ModelRow,
    Point,
    Policy,
)
from retailops_ai.qualified_anomalies.features import TABLES, Features, model_row
from retailops_ai.qualified_anomalies.store import Manifest, build, material, verify
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256
from retailops_ai.source_snapshot.importer import import_snapshot

ROOT = Path(__file__).parents[1]
PRODUCT = str(UUID(int=1))
LOCATION = str(UUID(int=2))
STOCK = str(UUID(int=3))
TARGET = date(2026, 8, 1)


def empty_tables():
    return {name: [] for name in TABLES}


def history(kind="sale_completed", currency="PLN", target=TARGET):
    days = []
    accepted = []
    for i in range(-35, 1):
        business = target + timedelta(days=i)
        end = datetime.combine(business + timedelta(days=1), datetime.min.time(), UTC)
        identifier = str(UUID(int=100 + i))
        days.append(
            Day(
                event_type=kind,
                business_date=business.isoformat(),
                product_id=PRODUCT,
                selling_location_id=LOCATION,
                channel="store",
                currency=currency,
                window_end=end.isoformat(),
                known_at=end.isoformat(),
                source_complete=True,
                activity="open",
                expected_business_ids=[identifier],
                required_sale_ids=[],
            )
        )
        accepted.append(
            {
                "event_type": kind,
                "business_id": identifier,
                "quantity": 10 if i < 0 else 25,
                "status": "refunded" if kind == "return_completed" else "completed",
                "amount": "10.00",
                "available_at": end.isoformat(),
            }
        )
    return days, {"accepted_facts": accepted, "quarantine": []}


def project(days, replay, target=None, tables=None, policy=None, raw=b""):
    gate = DayGate(days, replay, raw, ParentFacts([], {}))
    return Features(gate, tables or empty_tables(), policy).point(target or days[-1])


def test_packaged_contracts_are_strict_and_current():
    for name, model in (
        ("point", Point),
        ("policy", Policy),
        ("context", Context),
        ("model-row", ModelRow),
        ("manifest", Manifest),
    ):
        schema = json.loads(
            files("retailops_ai.qualified_anomalies")
            .joinpath(f"contracts/{name}.schema.json")
            .read_bytes()
        )
        assert schema == {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **model.model_json_schema(),
        }
        Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("kind,delay", [("sale_completed", 24), ("return_completed", 72)])
def test_qualified_history_and_exact_scoring_clock(kind, delay):
    days, replay = history(kind)
    point = project(days, replay)
    assert point.status == "ready_input" and point.observation.observed_units == 25
    assert point.expected_units == 10 and point.residual_units == 15
    # The last historical day's closure is not yet known at start minus 1us.
    assert point.usable_history_days == 27 and point.residual_count == 20
    assert point.history[-1].status == "closure_unavailable"
    assert point.robust_scale_units == 1 and point.scale_floor_applied
    assert point.detector_readiness == "not_qualified"
    assert point.scoring_origin == datetime(2026, 8, 2, tzinfo=UTC) + timedelta(hours=delay)


@pytest.mark.parametrize("late", ["receipt", "closure"])
def test_one_microsecond_after_fit_never_rewrites_history(late):
    days, replay = history()
    target = days[-1]
    original = project(days, replay)
    lag = next(d for d in days if d.business_date == "2026-07-25")
    after = (original.fit_cutoff + timedelta(microseconds=1)).isoformat()
    if late == "closure":
        days[days.index(lag)] = lag.model_copy(update={"known_at": after})
    else:
        next(
            f for f in replay["accepted_facts"] if f["business_id"] == lag.expected_business_ids[0]
        )["available_at"] = after
    point = project(days, replay, target)
    assert point.status == "insufficient_history" and point.expected_units is None
    assert point.residual_units is None and point.standardized_residual is None
    missing = next(p for p in point.history if p.business_date == lag.business_date)
    assert missing.status == ("closure_unavailable" if late == "closure" else "dq_missing_facts")


def test_repair_qualifies_later_fits_only():
    days, replay = history(target=date(2026, 8, 8))
    earlier = next(d for d in days if d.business_date == "2026-08-01")
    affected = next(d for d in days if d.business_date == "2026-07-25")
    fact = next(
        f for f in replay["accepted_facts"] if f["business_id"] == affected.expected_business_ids[0]
    )
    fact["available_at"] = "2026-08-02T00:00:00+00:00"
    before = project(days, replay, earlier)
    later = project(days, replay)
    assert (
        next(p for p in before.history if p.business_date == affected.business_date).status
        == "dq_missing_facts"
    )
    assert (
        next(p for p in later.history if p.business_date == affected.business_date).status
        == "qualified"
    )
    assert project(days, replay, earlier) == before


def test_outcome_never_changes_expected_or_scale():
    days, replay = history()
    before = project(days, replay)
    replay["accepted_facts"][-1]["quantity"] = 100000
    after = project(days, replay)
    assert (
        after.history,
        after.expected_units,
        after.robust_scale_units,
        after.residual_count,
    ) == (before.history, before.expected_units, before.robust_scale_units, before.residual_count)
    assert after.residual_units == 99990


@pytest.mark.parametrize("offset,qualified", [(0, True), (1, False)])
def test_scoring_receipt_boundary_is_inclusive(offset, qualified):
    days, replay = history()
    origin = project(days, replay).scoring_origin
    replay["accepted_facts"][-1]["available_at"] = (
        origin + timedelta(microseconds=offset)
    ).isoformat()
    point = project(days, replay)
    assert (point.status == "ready_input") == qualified
    assert (point.residual_units is not None) == qualified


@pytest.mark.parametrize(
    "change,status",
    [
        ("closed", "location_closed"),
        ("incomplete", "source_incomplete"),
        ("missing", "dq_missing_facts"),
        ("closure", "closure_unavailable"),
    ],
)
def test_unknown_days_never_supply_zero_or_residual(change, status):
    days, replay = history()
    day = days[-1]
    if change == "closed":
        day = day.model_copy(update={"activity": "closed"})
    elif change == "incomplete":
        day = day.model_copy(update={"source_complete": False})
    elif change == "missing":
        replay["accepted_facts"].pop()
    else:
        day = day.model_copy(update={"known_at": "2026-10-01T00:00:00+00:00"})
    days[-1] = day
    point = project(days, replay)
    assert point.status == "day_unqualified" and point.observation.status == status
    assert point.observation.observed_units is None and point.residual_units is None
    assert status in point.reason_codes


def test_explicit_closed_source_zero_is_a_valid_value():
    days, replay = history()
    days[-1] = days[-1].model_copy(update={"expected_business_ids": []})
    replay["accepted_facts"].pop()
    point = project(days, replay)
    assert point.status == "ready_input" and point.observation.observed_units == 0
    assert point.residual_units == -10


def test_event_type_currency_and_rejected_return_units_stay_separate():
    sales, sale_replay = history()
    returns, return_replay = history("return_completed")
    euros, euro_replay = history("sale_completed", "EUR")
    # Make business IDs distinct across currencies; DQ business keys are global.
    for day, fact in zip(euros, euro_replay["accepted_facts"], strict=True):
        identifier = str(UUID(int=1000 + int(UUID(fact["business_id"]))))
        fact["business_id"] = identifier
        fact["quantity"] = 100
        euros[euros.index(day)] = day.model_copy(update={"expected_business_ids": [identifier]})
    return_replay["accepted_facts"][-1].update(status="rejected", quantity=99, amount="0.00")
    replay = {
        "accepted_facts": sale_replay["accepted_facts"]
        + return_replay["accepted_facts"]
        + euro_replay["accepted_facts"],
        "quarantine": [],
    }
    days = sales + returns + euros
    sale = project(days, replay, sales[-1])
    ret = project(days, replay, returns[-1])
    eur = project(days, replay, euros[-1])
    assert sale.expected_units == ret.expected_units == 10 and eur.expected_units == 100
    assert ret.observation.observed_units == 0 and ret.observation.rejected_units == 99
    assert ret.residual_units == -10 and sale.observation.observed_units == 25


def test_context_uses_fit_plans_scoring_stock_and_never_aggregate_quantities():
    days, replay = history()
    fit = project(days, replay).fit_cutoff
    scoring = project(days, replay).scoring_origin
    tables = empty_tables()
    plan = {
        "product_id": PRODUCT,
        "selling_location_id": None,
        "channel": "all",
        "scope": "global",
        "currency": "PLN",
        "plan_key": "price",
        "version": 1,
        "effective_from": TARGET,
        "effective_to": TARGET + timedelta(days=10),
        "curated_available_at": fit,
        "source_record_sha256": "a" * 64,
        "price": Decimal("8.50"),
    }
    tables["price_plans"] = [
        plan,
        {**plan, "plan_key": "eur-price", "currency": "EUR", "price": Decimal("2.00")},
    ]
    promotion = {
        **plan,
        "promotion_key": "promo",
        "status": "active",
        "priority": 1,
        "curated_available_at": fit + timedelta(microseconds=1),
    }
    tables["promotion_plans"] = [promotion]
    mapping = {
        "product_id": PRODUCT,
        "selling_location_id": LOCATION,
        "channel": "store",
        "business_date": TARGET,
        "mapped_stock_location_id": STOCK,
        "curated_available_at": scoring,
        "source_record_sha256": "b" * 64,
        "version": 1,
        "observed_units": 100000,
    }
    tables["daily_demand_versions"] = [mapping]
    stock = {
        "product_id": PRODUCT,
        "stock_location_id": STOCK,
        "business_date": TARGET,
        "curated_available_at": scoring,
        "source_record_sha256": "c" * 64,
        "is_full_business_day": True,
        "status": "known",
        "snapshot_at": scoring - timedelta(hours=1),
        "on_hand": 0,
    }
    tables["inventory_daily_snapshots"] = [
        stock,
        {**stock, "snapshot_at": scoring + timedelta(microseconds=1), "on_hand": 444},
        {
            **stock,
            "snapshot_at": scoring,
            "curated_available_at": scoring + timedelta(microseconds=1),
            "on_hand": 999,
        },
    ]
    point = project(days, replay, tables=tables)
    assert point.context.planned_price == "8.50" and not point.context.promotion_offered
    assert point.context.on_hand == 0 and point.context.stock_status == "potential_stockout"
    assert point.observation.observed_units == 25 and point.expected_units == 10
    mapping["observed_units"] = 0
    assert project(days, replay, tables=tables) == point


def test_only_numeric_allowlist_reaches_model_and_unknown_input_is_withheld():
    days, replay = history()
    point = project(days, replay)
    numeric = model_row(point)
    assert numeric is not None and tuple(numeric.model_dump()) == MODEL_FEATURES
    assert numeric.residual_units == 15
    for forbidden in (
        "label",
        "seed",
        "magnitude",
        "history",
        "business_date",
        "source_record_sha256",
    ):
        with pytest.raises(ValueError):
            ModelRow.model_validate({**numeric.model_dump(), forbidden: 1})
    replay["accepted_facts"].pop()
    assert model_row(project(days, replay)) is None


def test_unattributed_quarantine_suspends_inputs_after_its_receipt():
    days, replay = history()
    raw = (
        canonical_json(
            {"record_id": "unknown", "body_utf8": json.dumps({"event_type": "unsupported"})}
        )
        + b"\n"
    )
    receipt = project(days, replay).scoring_origin
    replay["quarantine"] = [{"raw_ref": "unknown", "received_at": receipt.isoformat()}]
    point = project(days, replay, raw=raw)
    assert point.observation.status == "dq_unattributed_quarantine" and point.residual_units is None
    assert point.usable_history_days == 27 and point.expected_units == 10
    replay["quarantine"][0]["received_at"] = (receipt + timedelta(microseconds=1)).isoformat()
    assert project(days, replay, raw=raw).status == "ready_input"


@pytest.mark.parametrize(
    "mutation",
    ["outcome_history", "currency", "future_clock", "zero_unknown", "residual", "counts"],
)
def test_contract_rejects_resealed_invalid_features(mutation):
    days, replay = history()
    row = project(days, replay).model_dump(mode="json")
    if mutation == "outcome_history":
        row["history"][0]["business_date"] = row["business_date"]
    elif mutation == "currency":
        row["history"][0]["currency"] = "EUR"
    elif mutation == "future_clock":
        row["history"][0]["as_of"] = row["scoring_origin"]
    elif mutation == "zero_unknown":
        row["history"][-1]["observed_units"] = 0
    elif mutation == "residual":
        row["residual_units"] = 0
    else:
        row["usable_history_days"] = 28
    with pytest.raises(ValueError):
        Point.model_validate_json(canonical_json(row))


@pytest.fixture(scope="module", params=["demand", "physical"])
def prepared(request, tmp_path_factory):
    root = tmp_path_factory.mktemp("qualified-features-" + request.param)
    case = request.param
    for name in ("full-raw-dq-v2", "day-coverage-v1"):
        archive = ROOT / f"data/fixtures/{name}.zip"
        lineage = json.loads((ROOT / f"data/fixtures/{name}.lineage.json").read_bytes())
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == lineage["archive_sha256"]
        with ZipFile(archive) as zipped:
            for member, spec in lineage["cases"][case]["files"].items():
                raw = zipped.read(member)
                assert (
                    hashlib.sha256(raw).hexdigest() == spec["sha256"] and len(raw) == spec["bytes"]
                )
                destination = root / "inputs" / name / member
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
    generated = root / "parents/data/generated"
    source = import_snapshot(
        root / "inputs/full-raw-dq-v2" / case / "public",
        generated,
        required_use_cases=("anomaly_source",),
    )
    curated = build_curated(source.directory, generated)
    replay = build_replay(
        root / "inputs/full-raw-dq-v2" / case / "capture",
        curated.directory,
        source.directory,
        generated,
    )
    args = (
        Path(replay["directory"]),
        root / "inputs/day-coverage-v1" / case,
        curated.directory,
        source.directory,
    )
    before = {str(p): hashes(p) for p in args}
    result = build(*args, root / "output/data/generated")
    return {"root": root, "args": args, "result": result, "before": before}


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_native_artifact_verification_statuses_and_immutable_parents(prepared):
    p = prepared
    result = p["result"]
    root = Path(result["directory"])
    manifest = verify(root, *p["args"])
    assert manifest.qualified_anomaly_input_id == result["qualified_anomaly_input_id"]
    assert manifest.descriptor.return_scope == "purchases_in_parent_source_only"
    assert manifest.descriptor.detector_readiness == "not_qualified"
    assert all(
        result["status_counts"].get(s)
        for s in ("ready_input", "insufficient_history", "day_unqualified")
    )
    for raw in (root / "features.jsonl").read_bytes().splitlines():
        point = Point.model_validate_json(raw)
        assert all(q.as_of == point.fit_cutoff.isoformat() for q in point.history)
        assert point.observation.as_of == point.scoring_origin.isoformat()
    assert {str(p): hashes(p) for p in p["args"]} == p["before"]


def test_native_resealed_feature_tamper_is_reconstructed(prepared):
    p = prepared
    target = p["root"] / "tampered"
    shutil.copytree(p["result"]["directory"], target)
    rows = [json.loads(line) for line in (target / "features.jsonl").read_bytes().splitlines()]
    # Still structurally valid context, with a refreshed manifest and both seals.
    rows[0]["context"]["planned_price"] = "999.00"
    payload = b"".join(canonical_json(row) + b"\n" for row in rows)
    document = json.loads((target / "feature_manifest.json").read_bytes())
    document["descriptor"]["rows_sha256"] = hashlib.sha256(payload).hexdigest()
    document["qualified_anomaly_input_id"] = "qualified-anomaly-inputs-sha256-" + json_sha256(
        document["descriptor"]
    )
    raw = canonical_json(document) + b"\n"
    (target / "features.jsonl").write_bytes(payload)
    (target / "feature_manifest.json").write_bytes(raw)
    (target / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(SnapshotError, match="identity_runtime_or_parent_mismatch"):
        verify(target, *p["args"])


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_output_aliases_rejected_without_reading_parents(prepared, tmp_path, alias):
    target = tmp_path / "alias"
    shutil.copytree(prepared["result"]["directory"], target)
    victim = target / "features.jsonl"
    victim.unlink()
    original = Path(prepared["result"]["directory"]) / "features.jsonl"
    if alias == "symlink":
        victim.symlink_to(original)
    else:
        victim.hardlink_to(original)
    try:
        with pytest.raises((SnapshotError, ValueError)):
            verify(target, *prepared["args"])
    finally:
        victim.unlink()


def test_policy_changes_identity_and_unsafe_publication_is_rejected(prepared):
    p = prepared
    expected, _ = material(*p["args"], Policy(sales_delay_hours=0, returns_delay_hours=0))
    assert expected.qualified_anomaly_input_id != p["result"]["qualified_anomaly_input_id"]
    with pytest.raises(SnapshotError, match="separate_generated_root"):
        build(*p["args"], p["args"][0] / "data/generated")
