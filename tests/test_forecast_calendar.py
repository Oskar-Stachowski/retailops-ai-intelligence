"""Forecast boundary, calendar arithmetic, manifest identity and real curated integration."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from retailops_ai.curated.builder import build_curated
from retailops_ai.curated.reader import rows_as_of
from retailops_ai.data_contracts.common import SellingKey
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.calendar import (
    build_calendar,
    default_task,
    implementation,
    load_calendar,
    publish_calendar,
    rows_for_origin,
)
from retailops_ai.forecasting.cli import main
from retailops_ai.forecasting.contract import (
    HORIZONS,
    CalendarManifest,
    Origin,
    OriginWindow,
    TaskConfig,
    make_origin,
)
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.source_snapshot.importer import import_snapshot

ROOT = Path(__file__).resolve().parents[1]
WINDOW = OriginWindow(start=date(2026, 7, 16), end=date(2026, 7, 17))


@pytest.fixture(scope="module")
def curated(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("forecast-source").resolve()
    imported = import_snapshot(
        ROOT / "data/fixtures/ai-smoke-v1/snapshot", workspace / "data/generated"
    )
    return build_curated(imported.directory, workspace / "data/generated")


@pytest.fixture(scope="module")
def calendar(curated):
    return build_calendar(curated.directory, WINDOW)


@pytest.mark.parametrize(
    "day,first,last",
    [
        (date(2024, 2, 28), date(2024, 2, 29), date(2024, 3, 13)),
        (date(2025, 2, 28), date(2025, 3, 1), date(2025, 3, 14)),
        (date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 14)),
        (date(2026, 3, 28), date(2026, 3, 29), date(2026, 4, 11)),
        (date(2026, 10, 24), date(2026, 10, 25), date(2026, 11, 7)),
    ],
)
def test_calendar_days_across_leap_year_month_year_and_european_dst(day, first, last):
    origin = make_origin(day)
    assert origin.targets[0].target_date == first and origin.targets[-1].target_date == last
    assert origin.forecast_origin == datetime.combine(day, datetime.min.time(), UTC) + timedelta(
        seconds=86399
    )
    assert origin.availability_cutoff == origin.forecast_origin
    assert origin.reporting_windows[0].start == first
    assert origin.reporting_windows[1].end == last
    assert all((t.target_date - day).days == t.horizon_days for t in origin.targets)
    key = SellingKey(product_id="p-1", selling_location_id="s-1", channel="store")
    keys = origin.forecast_keys(key)
    assert len(keys) == len({k.target_date for k in keys}) == 14
    assert {k.forecast_origin for k in keys} == {origin.forecast_origin}


@pytest.mark.parametrize(
    "field,value",
    [
        ("business_timezone", "Europe/Warsaw"),
        ("cutoff_time_utc", "00:00:00"),
        ("schema_version", "2.0.0"),
        ("availability_delay_seconds", 3600),
        ("availability_delay_seconds", False),
        ("horizon_days", [7, 14]),
        ("horizon_days", list(range(14, 0, -1))),
        ("horizon_days", [1] * 14),
        ("horizon_days", [True, *range(2, 15)]),
        ("reporting_windows_days", [14, 7]),
        ("inventory_features_enabled", True),
        ("inventory_features_enabled", 0),
        ("simulation_truth_features_enabled", True),
        ("source_operational_outputs_as_targets", True),
        ("evaluation_protocol", "rolling_one_step"),
        ("target_type", "latent_demand"),
        ("observation_grain", ["business_date", "product_id", "stock_location_id", "channel"]),
    ],
)
def test_task_rejects_silent_policy_changes_and_coercion(field, value):
    payload = default_task().model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        TaskConfig.model_validate_json(json.dumps(payload))


def test_resolved_policy_matches_wire_semantics_and_is_packaged():
    assert default_task() == TaskConfig()
    assert default_task().horizon_days == HORIZONS
    assert default_task().availability_delay_seconds == 0
    assert default_task().cutoff_policy == "end_of_day_second_v1"
    assert len(default_task().task_id()) == len("forecast-task-sha256-") + 64


@pytest.mark.parametrize(
    "mutation", ["naive", "offset", "precision", "cutoff", "target", "window", "duplicate"]
)
def test_origin_rejects_inconsistent_or_advanced_boundary(mutation):
    data = make_origin(date(2026, 7, 16)).model_dump(mode="json")
    if mutation == "naive":
        data["forecast_origin"] = "2026-07-16T23:59:59"
    elif mutation == "offset":
        data["forecast_origin"] = "2026-07-16T23:59:59+02:00"
    elif mutation == "precision":
        data["forecast_origin"] = "2026-07-16T23:59:59.000001Z"
    elif mutation == "cutoff":
        data["availability_cutoff"] = "2026-07-17T23:59:59Z"
    elif mutation == "target":
        data["targets"][2]["target_date"] = "2026-07-20"
    elif mutation == "window":
        data["reporting_windows"][0]["end"] = "2026-07-30"
    else:
        data["targets"][2] = data["targets"][1]
    with pytest.raises(ValidationError):
        Origin.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "start,end",
    [
        (date(2026, 7, 2), date(2026, 7, 1)),
        (date(2026, 1, 1), date(2027, 1, 2)),
        (date.max, date.max),
    ],
)
def test_calendar_rejects_unbounded_invalid_and_overflow_windows(start, end):
    with pytest.raises(ValidationError):
        OriginWindow(start=start, end=end)


def test_real_curated_binding_determinism_and_transport_independence(curated, calendar):
    repeated = build_calendar(
        curated.directory, WINDOW, generated_at=datetime(2027, 1, 1, tzinfo=UTC)
    )
    assert repeated.calendar_id == calendar.calendar_id
    assert repeated.generated_at != calendar.generated_at
    assert calendar.descriptor.parent.curated_dataset_id == curated.manifest["curated_dataset_id"]
    assert (
        calendar.descriptor.parent.source_dataset_id
        == curated.manifest["descriptor"]["parent_source_dataset_id"]
    )
    assert len(calendar.origins) == 2 and calendar.forecast_model_status == "not_ready"
    changed = build_calendar(
        curated.directory, OriginWindow(start=WINDOW.start, end=WINDOW.end + timedelta(days=1))
    )
    assert changed.calendar_id != calendar.calendar_id
    data = calendar.model_dump(mode="json")
    data["descriptor"]["parent"]["source_dataset_id"] = "source-sha256-" + "e" * 64
    data["calendar_id"] = "forecast-calendar-sha256-" + canonical_sha256(data["descriptor"])
    different_parent = CalendarManifest.model_validate_json(json.dumps(data))
    assert different_parent.calendar_id != calendar.calendar_id
    with pytest.raises(SnapshotError, match="parent_mismatch"):
        list(rows_for_origin(curated.directory, different_parent, WINDOW.start))
    assert implementation() == calendar.descriptor.implementation


@pytest.mark.parametrize(
    "mutation", ["target", "origin_gap", "identity", "content", "task_id", "code", "unknown_field"]
)
def test_manifest_rejects_tampering(calendar, mutation):
    data = deepcopy(calendar.model_dump(mode="json"))
    if mutation == "target":
        data["origins"][0]["targets"][0]["target_date"] = "2026-08-01"
    elif mutation == "origin_gap":
        data["origins"] = data["origins"][:1]
    elif mutation == "unknown_field":
        data["hidden_truth"] = 100
    elif mutation == "code":
        data["descriptor"]["implementation"]["code_files"]["forecasting/contract.py"] = "f" * 64
    elif mutation == "task_id":
        data["descriptor"]["task_id"] = "forecast-task-sha256-" + "f" * 64
    elif mutation == "content":
        data["descriptor"]["calendar_content_sha256"] = "f" * 64
    else:
        data["calendar_id"] = "forecast-calendar-sha256-" + "f" * 64
    with pytest.raises(ValidationError):
        CalendarManifest.model_validate_json(json.dumps(data))


def test_cutoff_keeps_microsecond_precision_and_late_version_is_not_learned_mid_forecast(
    curated, calendar, monkeypatch
):
    from retailops_ai.curated import reader

    origin = calendar.origins[0]
    facts = (
        {"version": 1, "available_at": origin.forecast_origin},
        {"version": 2, "available_at": origin.forecast_origin + timedelta(microseconds=1)},
        {"version": 3, "available_at": origin.forecast_origin + timedelta(days=1)},
    )

    def controlled_versions(root, cutoff, **kwargs):
        # Controlled upstream timeline; reader behavior at this boundary is separately
        # covered by curated integration tests. This checks the forecast wrapper's cutoff.
        eligible = [r for r in facts if r["available_at"] <= cutoff]
        yield max(eligible, key=lambda r: r["version"])

    monkeypatch.setattr(reader, "rows_as_of", controlled_versions)
    for _ in (1, 7, 14):
        assert list(rows_for_origin(curated.directory, calendar, origin.origin_date)) == [facts[0]]
    assert list(rows_for_origin(curated.directory, calendar, WINDOW.end)) == [facts[2]]


def test_full_precision_cutoff_on_real_curated_and_no_future_history(curated, calendar):
    origin = calendar.origins[0]
    actual = list(rows_for_origin(curated.directory, calendar, WINDOW.start))
    independent = list(rows_as_of(curated.directory, origin.forecast_origin))
    assert actual == independent and actual
    assert all(r["curated_available_at"] <= origin.forecast_origin for r in actual)
    assert all(r["business_date"] <= origin.origin_date for r in actual)
    # Advancing to a NEW origin can reveal new observations, never inside the frozen 14-day run.
    tomorrow = list(rows_for_origin(curated.directory, calendar, WINDOW.end))
    assert len(tomorrow) > len(actual)
    with pytest.raises(SnapshotError, match="does_not_advance"):
        list(
            rows_for_origin(
                curated.directory,
                calendar,
                WINDOW.start,
                target_date=origin.targets[-1].target_date,
            )
        )
    with pytest.raises(SnapshotError, match="outside_calendar"):
        list(rows_for_origin(curated.directory, calendar, date(2026, 8, 1)))


def test_all_plan_horizons_use_one_origin_not_target_day(curated, calendar, monkeypatch):
    from retailops_ai.curated import reader

    calls = []
    original = reader.rows_as_of

    def traced(root, origin, **kwargs):
        calls.append((origin, kwargs["business_date"]))
        yield from original(root, origin, **kwargs)

    monkeypatch.setattr(reader, "rows_as_of", traced)
    origin = calendar.origins[0]
    for target in (origin.targets[0], origin.targets[6], origin.targets[-1]):
        plans = list(
            rows_for_origin(
                curated.directory,
                calendar,
                WINDOW.start,
                table="price_plans",
                target_date=target.target_date,
            )
        )
        assert plans and all(r["curated_available_at"] <= origin.availability_cutoff for r in plans)
        assert all(r["effective_from"] <= target.target_date < r["effective_to"] for r in plans)
    assert {at for at, _ in calls} == {origin.forecast_origin}
    assert len({day for _, day in calls}) == 3
    for table in ("daily_demand_observations", "inventory", "truth", "forecasts"):
        with pytest.raises(SnapshotError, match="supported_plan"):
            list(
                rows_for_origin(
                    curated.directory,
                    calendar,
                    WINDOW.start,
                    table=table,
                    target_date=origin.targets[0].target_date,
                )
            )


def test_manifest_publication_concurrent_reuse_keeps_original_bytes(calendar, tmp_path):
    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(lambda _: publish_calendar(calendar, tmp_path), range(2)))
    assert paths[0] == paths[1]
    original = paths[0].read_bytes()
    data = calendar.model_dump(mode="json")
    data["generated_at"] = "2027-01-01T00:00:00Z"
    changed_runtime = CalendarManifest.model_validate_json(json.dumps(data))
    assert publish_calendar(changed_runtime, tmp_path).read_bytes() == original
    assert load_calendar(paths[0]) == calendar
    assert not list(tmp_path.glob(".forecast-calendar-*"))
    paths[0].write_bytes(b"{}")
    with pytest.raises(ValidationError):
        publish_calendar(calendar, tmp_path)
    assert paths[0].read_bytes() == b"{}"


def test_json_duplicate_keys_and_symlink_input_are_rejected(calendar, tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version":"1.0.0","schema_version":"1.0.0"}')
    with pytest.raises(SnapshotError, match="duplicate_json_key"):
        load_calendar(path)
    path.write_text(calendar.model_dump_json())
    linked = tmp_path / "linked.json"
    linked.symlink_to(path)
    with pytest.raises(OSError):
        load_calendar(linked)


def test_schemas_and_cli_roundtrip(curated, calendar, tmp_path, capsys):
    for family, value in (
        ("task", default_task().model_dump(mode="json")),
        ("calendar_manifest", calendar.model_dump(mode="json")),
    ):
        schema = json.loads((ROOT / f"contracts/forecast/v1/{family}.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    assert main(["task-check"]) == 0
    assert json.loads(capsys.readouterr().out)["task_id"] == default_task().task_id()
    assert (
        main(
            [
                "calendar-build",
                "--curated-dir",
                str(curated.directory),
                "--origin-from",
                "2026-07-16",
                "--origin-to",
                "2026-07-17",
                "--output-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    built = json.loads(capsys.readouterr().out)
    assert built["daily_targets"] == 28
    assert (
        main(
            [
                "calendar-verify",
                "--manifest",
                built["manifest"],
                "--curated-dir",
                str(curated.directory),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "verified"
    assert (
        main(
            [
                "calendar-build",
                "--curated-dir",
                str(curated.directory),
                "--origin-from",
                "2026-07-17",
                "--origin-to",
                "2026-07-16",
            ]
        )
        == 2
    )
    rejected = capsys.readouterr()
    assert rejected.out == "" and json.loads(rejected.err)["error"] == "forecast_rejected"
    assert str(curated.directory) not in rejected.err
