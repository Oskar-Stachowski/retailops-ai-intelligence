"""Independent closures, historical receipts, explicit zero and unknown DQ semantics."""

import hashlib
import json
import shutil
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest
from jsonschema import Draft202012Validator

from retailops_ai.curated.builder import build_curated
from retailops_ai.day_qualification.contract import GRAIN, KEY, Coverage, Day, Point, Policy
from retailops_ai.day_qualification.gate import DayGate
from retailops_ai.day_qualification.parents import parents, verify_coverage
from retailops_ai.day_qualification.store import Manifest, build, verify
from retailops_ai.full_raw_dq.store import build_replay
from retailops_ai.raw_dq.contract import stamp
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256
from retailops_ai.source_snapshot.importer import import_snapshot, verify_import

ROOT = Path(__file__).parents[1]


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def grain(day):
    return tuple(getattr(day, k) for k in GRAIN)


@pytest.fixture(scope="module", params=["demand", "physical"])
def prepared(request, tmp_path_factory):
    root = tmp_path_factory.mktemp("day-qualification-" + request.param)
    case = request.param
    inputs = root / "inputs"
    for fixture in ("full-raw-dq-v2", "day-coverage-v1"):
        lineage = json.loads((ROOT / f"data/fixtures/{fixture}.lineage.json").read_bytes())
        path = ROOT / f"data/fixtures/{fixture}.zip"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == lineage["archive_sha256"]
        with ZipFile(path) as archive:
            members = lineage["cases"][case]["files"]
            for name, spec in members.items():
                raw = archive.read(name)
                assert (
                    len(raw) == spec["bytes"] and hashlib.sha256(raw).hexdigest() == spec["sha256"]
                )
                destination = inputs / fixture / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
    generated = root / "data/generated"
    imported = import_snapshot(
        inputs / "full-raw-dq-v2" / case / "public",
        generated,
        required_use_cases=("anomaly_source",),
    )
    curated = build_curated(imported.directory, generated)
    result = build_replay(
        inputs / "full-raw-dq-v2" / case / "capture",
        curated.directory,
        imported.directory,
        generated,
    )
    args = (
        Path(result["directory"]),
        inputs / "day-coverage-v1" / case,
        curated.directory,
        imported.directory,
    )
    full, coverage, days, replay, raw, parent = parents(*args)
    source = verify_import(imported.directory, required_use_cases=("anomaly_source",)).manifest[
        "source"
    ]["descriptor"]
    return {
        "root": root,
        "args": args,
        "days": days,
        "replay": replay,
        "raw": raw,
        "parent": parent,
        "coverage": coverage,
        "source": source,
        "gate": DayGate(days, replay, raw, parent),
        "full": full,
    }


def test_contracts_are_versioned_packaged_and_strict():
    from importlib.resources import files

    for name, model in (
        ("day", Day),
        ("coverage", Coverage),
        ("point", Point),
        ("policy", Policy),
        ("manifest", Manifest),
    ):
        schema = json.loads(
            files("retailops_ai.day_qualification")
            .joinpath(f"contracts/{name}.schema.json")
            .read_bytes()
        )
        assert schema == {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **model.model_json_schema(),
        }
        Draft202012Validator.check_schema(schema)


def test_unknown_status_cannot_supply_a_score_or_zero():
    base = {
        "event_type": "sale_completed",
        "business_date": "2026-07-02",
        "product_id": "00000000-0000-0000-0000-000000000001",
        "selling_location_id": "00000000-0000-0000-0000-000000000002",
        "channel": "store",
        "currency": "PLN",
        "as_of": "2026-07-04T00:00:00+00:00",
        "status": "closure_unavailable",
    }
    for field, value in (
        ("observed_units", 0),
        ("amount", "0.00"),
        ("rejected_units", 0),
        ("score_eligible", True),
        ("raw_dq_completeness", "qualified"),
    ):
        with pytest.raises(ValueError, match="unknown_day_cannot_supply_value"):
            Point(**base, **{field: value})


def test_closure_is_not_progress_max_event_or_cohort_maturity(prepared):
    p = prepared
    day = next(
        d for d in p["days"] if d.event_type == "return_completed" and d.expected_business_ids
    )
    before = (stamp(day.known_at) - timedelta(microseconds=1)).isoformat()
    point = p["gate"].point(grain(day), before)
    assert point.status == "closure_unavailable" and point.observed_units is None
    assert point.missing_fact_keys == [] and point.score_eligible is False
    replay = deepcopy(p["replay"])
    replay["progress"] = [{"complete_through": "2099-01-01T00:00:00+00:00"}]
    replay["report"]["max_event_time"] = "2099-01-01T00:00:00+00:00"
    assert DayGate(p["days"], replay, p["raw"], p["parent"]).point(grain(day), before) == point


def test_missing_and_closed_sales_never_become_zero(prepared):
    original = next(d for d in prepared["days"] if d.event_type == "sale_completed")
    missing = original.model_copy(update={"source_complete": False, "activity": "missing"})
    gate = DayGate([missing], prepared["replay"], prepared["raw"], prepared["parent"])
    point = gate.point(grain(missing), "2026-10-01T00:00:00+00:00")
    assert (
        point.status == "source_incomplete"
        and point.observed_units is None
        and not point.score_eligible
    )
    for activity, status in (("closed", "location_closed"),):
        day = next(
            d
            for d in prepared["days"]
            if d.event_type == "sale_completed" and d.activity == activity
        )
        point = prepared["gate"].point(grain(day), "2026-10-01T00:00:00+00:00")
        assert point.status == status and point.observed_units is None and not point.score_eligible
    day = prepared["days"][0]
    absent = (day.event_type, "2026-01-01", *(getattr(day, k) for k in KEY))
    assert prepared["gate"].point(absent, "2026-10-01T00:00:00+00:00").status == "no_declaration"


def test_explicit_known_zero_requires_closed_source_and_all_purchase_receipts(prepared):
    p = prepared
    later = "2026-10-01T00:00:00+00:00"
    for kind in ("sale_completed", "return_completed"):
        candidates = [
            d
            for d in p["days"]
            if d.event_type == kind
            and d.source_complete
            and d.activity == "open"
            and not d.expected_business_ids
        ]
        day = next(d for d in candidates if p["gate"].point(grain(d), later).status == "qualified")
        point = p["gate"].point(grain(day), later)
        assert (
            point.observed_units == point.rejected_units == 0
            and point.amount == "0.00"
            and point.score_eligible
        )
        assert (
            p["gate"]
            .point(grain(day), (stamp(day.known_at) - timedelta(microseconds=1)).isoformat())
            .observed_units
            is None
        )
        if kind == "return_completed" and day.required_sale_ids:
            replay = deepcopy(p["replay"])
            replay["accepted_facts"] = [
                f for f in replay["accepted_facts"] if f["business_id"] != day.required_sale_ids[0]
            ]
            assert (
                DayGate(p["days"], replay, p["raw"], p["parent"]).point(grain(day), later).status
                == "dq_missing_facts"
            )


def test_six_missing_native_facts_withhold_their_days(prepared):
    p = prepared
    accepted = {(f["event_type"], f["business_id"]) for f in p["replay"]["accepted_facts"]}
    missing = set(p["parent"].facts) - accepted
    assert len(missing) == 6
    for key in missing:
        fact = p["parent"].facts[key]
        point = p["gate"].point(tuple(fact[k] for k in GRAIN), "2026-10-01T00:00:00+00:00")
        if point.status == "source_incomplete":
            assert point.observed_units is None
        else:
            assert (
                point.status == "dq_missing_facts"
                and f"{key[0]}:{key[1]}" in point.missing_fact_keys
            )


def test_native_status_tail_and_delivery_clock_are_retained(prepared):
    p = prepared
    tail = [
        d
        for d in p["days"]
        if d.event_type == "return_completed"
        and d.business_date > "2026-07-31"
        and d.expected_business_ids
    ]
    assert tail
    rejected = next(
        f
        for f in p["replay"]["accepted_facts"]
        if f["status"] == "rejected"
        and p["gate"].point(tuple(f[k] for k in GRAIN), "2026-10-01T00:00:00+00:00").status
        == "qualified"
    )
    point = p["gate"].point(tuple(rejected[k] for k in GRAIN), "2026-10-01T00:00:00+00:00")
    assert point.status == "qualified" and point.rejected_units >= rejected["quantity"]
    members = [
        f
        for f in p["replay"]["accepted_facts"]
        if tuple(f[k] for k in GRAIN) == tuple(rejected[k] for k in GRAIN)
    ]
    assert point.observed_units == sum(f["quantity"] for f in members if f["status"] == "refunded")
    late = next(f for f in p["replay"]["accepted_facts"] if f["timing_status"] == "late")
    day = next(d for d in p["days"] if grain(d) == tuple(late[k] for k in GRAIN))
    # Hold every other prerequisite ready, then move one verified receipt across the cutoff.
    replay = deepcopy(p["replay"])
    cutoff = "2026-10-01T00:00:00+00:00"
    moved = next(f for f in replay["accepted_facts"] if f["business_id"] == late["business_id"])
    moved["available_at"] = cutoff
    gate = DayGate([day], replay, p["raw"], p["parent"])
    before = (stamp(cutoff) - timedelta(microseconds=1)).isoformat()
    old = gate.point(grain(day), before)
    assert old.status in {"dq_missing_facts", "source_incomplete"} and old.observed_units is None
    gate.point(grain(day), cutoff)
    assert gate.point(grain(day), before) == old


def test_late_native_replacement_qualifies_only_at_its_receipt(prepared):
    p = prepared
    cutoff = "2026-10-01T00:00:00+00:00"
    day = next(
        d
        for d in p["days"]
        if d.event_type == "sale_completed"
        and d.expected_business_ids
        and p["gate"].point(grain(d), cutoff).status == "qualified"
    )
    replay = deepcopy(p["replay"])
    moved = next(
        f
        for f in replay["accepted_facts"]
        if f["event_type"] == day.event_type and f["business_id"] == day.expected_business_ids[0]
    )
    moved["available_at"] = cutoff
    gate = DayGate([day], replay, p["raw"], p["parent"])
    before = (stamp(cutoff) - timedelta(microseconds=1)).isoformat()
    old = gate.point(grain(day), before)
    assert old.status == "dq_missing_facts" and old.observed_units is None
    assert gate.point(grain(day), cutoff).status == "qualified"
    assert gate.point(grain(day), before) == old


@pytest.mark.parametrize("body_change", ["empty", "conflicting_uuid"])
def test_unknown_quarantine_withholds_zero_only_after_its_receipt(prepared, body_change):
    p = prepared
    day = next(
        d
        for d in p["days"]
        if p["gate"].point(grain(d), "2026-10-01T00:00:00+00:00").status == "qualified"
        and not d.expected_business_ids
    )
    records = [json.loads(line) for line in p["raw"].splitlines()]
    replay = deepcopy(p["replay"])
    q = replay["quarantine"][0]
    record = next(r for r in records if r["record_id"] == q["raw_ref"])
    body = {} if body_change == "empty" else deepcopy(p["parent"].events[0])
    if body_change == "conflicting_uuid":
        body["payload"]["sale_id"] = "00000000-0000-0000-0000-000000000099"
    record["body_utf8"] = canonical_json(body).decode()
    q["received_at"] = "2026-10-01T00:00:00+00:00"
    raw = b"".join(canonical_json(r) + b"\n" for r in records)
    gate = DayGate([day], replay, raw, p["parent"])
    assert gate.point(grain(day), "2026-09-30T23:59:59+00:00").status == "qualified"
    point = gate.point(grain(day), q["received_at"])
    assert point.status == "dq_unattributed_quarantine" and point.observed_units is None


@pytest.mark.parametrize(
    "change", ["zero", "close_early", "grain", "scope", "parent", "private", "omit", "bool_integer"]
)
def test_resealed_false_declarations_are_rejected(prepared, tmp_path, change):
    p = prepared
    directory = tmp_path / "coverage"
    shutil.copytree(p["args"][1], directory)
    rows = [json.loads(line) for line in (directory / "days.jsonl").read_bytes().splitlines()]
    doc = json.loads((directory / "coverage_manifest.json").read_bytes())
    row = next(r for r in rows if r["expected_business_ids"])
    if change == "zero":
        row["expected_business_ids"] = []
    elif change == "close_early":
        row["known_at"] = "2026-01-01T00:00:00+00:00"
    elif change == "grain":
        row["currency"] = "EUR" if row["currency"] == "PLN" else "PLN"
    elif change == "scope":
        doc["descriptor"]["return_scope"] = "global_all_purchases"
    elif change == "parent":
        doc["descriptor"]["source_dataset_id"] = "source-sha256-" + "0" * 64
    elif change == "private":
        row["expected_action"] = "alert"
    elif change == "omit":
        rows.remove(row)
    else:
        row["source_complete"] = 1
    raw = b"".join(canonical_json(r) + b"\n" for r in rows)
    doc["descriptor"]["rows_sha256"] = hashlib.sha256(raw).hexdigest()
    doc["descriptor"]["row_count"] = len(rows)
    doc["coverage_id"] = "day-coverage-sha256-" + json_sha256(doc["descriptor"])
    encoded = canonical_json(doc) + b"\n"
    (directory / "days.jsonl").write_bytes(raw)
    (directory / "coverage_manifest.json").write_bytes(encoded)
    (directory / "manifest.sha256").write_text(hashlib.sha256(encoded).hexdigest() + "\n")
    with pytest.raises(ValueError):
        verify_coverage(directory, p["source"], p["days"])


@pytest.mark.parametrize(
    "change",
    ["extra_file", "symlink", "hardlink", "pretty", "duplicate_key", "borrowed_boolean_identity"],
)
def test_coverage_inventory_and_canonical_identity_guards(prepared, tmp_path, change):
    p = prepared
    directory = tmp_path / "coverage"
    shutil.copytree(p["args"][1], directory)
    manifest = directory / "coverage_manifest.json"
    original = manifest.read_bytes()
    if change == "extra_file":
        (directory / "simulation_truth.json").write_text("{}")
    elif change in {"symlink", "hardlink"}:
        saved = tmp_path / "manifest.saved"
        manifest.rename(saved)
        if change == "symlink":
            manifest.symlink_to(saved)
        else:
            manifest.hardlink_to(saved)
    elif change == "pretty":
        manifest.write_text(json.dumps(json.loads(original), indent=2) + "\n")
    elif change == "duplicate_key":
        manifest.write_bytes(original.replace(b"{", b'{"coverage_id":"shadow",', 1))
    else:
        manifest.write_bytes(
            original.replace(
                b'"cohort_maturity_is_day_closure":false', b'"cohort_maturity_is_day_closure":0'
            )
        )
    (directory / "manifest.sha256").write_text(
        hashlib.sha256(manifest.read_bytes()).hexdigest() + "\n"
    )
    with pytest.raises(ValueError):
        verify_coverage(directory, p["source"], p["days"])


def test_artifact_reconstructs_parents_and_rejects_resealed_qualified_values(prepared, tmp_path):
    p = prepared
    before = {str(root): hashes(root) for root in p["args"]}
    result = build(*p["args"], tmp_path / "data/generated")
    directory = Path(result["directory"])
    assert verify(directory, *p["args"]).day_qualification_id == result["day_qualification_id"]
    assert result["model_readiness"] == "not_qualified"
    assert (
        result["status_counts"]["qualified"] > 0 and result["status_counts"]["dq_missing_facts"] > 0
    )
    published = hashes(directory)
    repeated = build(*p["args"], tmp_path / "data/generated")
    assert (
        repeated["status"] == "reused"
        and repeated["day_qualification_id"] == result["day_qualification_id"]
        and hashes(directory) == published
    )
    assert {str(root): hashes(root) for root in p["args"]} == before
    rows = [
        json.loads(line) for line in (directory / "qualified_days.jsonl").read_bytes().splitlines()
    ]
    next(r for r in rows if r["status"] == "qualified")["observed_units"] += 1
    raw = b"".join(canonical_json(r) + b"\n" for r in rows)
    doc = json.loads((directory / "qualification_manifest.json").read_bytes())
    doc["descriptor"]["rows_sha256"] = hashlib.sha256(raw).hexdigest()
    doc["day_qualification_id"] = "day-qualification-sha256-" + json_sha256(doc["descriptor"])
    encoded = canonical_json(doc) + b"\n"
    (directory / "qualified_days.jsonl").write_bytes(raw)
    (directory / "qualification_manifest.json").write_bytes(encoded)
    (directory / "manifest.sha256").write_text(hashlib.sha256(encoded).hexdigest() + "\n")
    with pytest.raises(SnapshotError):
        verify(directory, *p["args"])
