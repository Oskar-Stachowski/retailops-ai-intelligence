"""Controlled reader witnesses exercise ordering and failure gates, not native Source proof."""

import copy
import hashlib
import json
import weakref
from types import SimpleNamespace

import pytest
from test_paired_source_comparison import tables as comparison_tables

from retailops_ai.evaluation_campaign import campaign_anomaly_truth_worker as ordinary_worker
from retailops_ai.evaluation_campaign import campaign_paired_truth_worker as worker


def injection(**changes):
    return {
        "id": "episode",
        "product_id": "product-a",
        "selling_location_id": "location",
        "channel": "store",
        "injection_type": "one_day_spike",
        "start_date": "2026-05-25",
        "end_date": "2026-05-25",
        **changes,
    }


def world():
    result = comparison_tables()
    result["product_catalog"] = [
        {"id": p["id"], "category_id": "category"} for p in result["products"]
    ]
    result["return_policies"] = [
        {
            "category_id": "category",
            "channel": "store",
            "window_days": "30",
            "max_ingestion_delay_days": "5",
            "known_at": "2026-01-01T00:00:00Z",
        }
    ]
    for row in result["daily_demand_observations"]:
        row.update(
            selling_location_id="location",
            channel="store",
            currency="PLN",
            quality_status="valid",
            source_data_complete="true",
            available_at=row["business_date"] + "T23:59:00Z",
        )
    result["inventory_sales"] = [
        {
            "product_id": p["id"],
            "selling_location_id": "location",
            "channel": "store",
            "currency": "PLN",
            "sold_at": "2026-05-24T12:00:00Z",
            "available_at": "2026-05-24T12:30:00Z",
        }
        for p in result["products"]
    ]
    return result


def scalar(key, value):
    # Deliberately small controlled producer witness, never a substitute for the
    # installed producer in a real Source read.
    return value == "true" if key == "source_data_complete" else int(value)


@pytest.fixture
def scalar_witness(monkeypatch):
    original = worker.importlib.import_module
    monkeypatch.setattr(
        worker.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(canonical_cell=scalar)
            if name == "data.generator.identity"
            else original(name)
        ),
    )


def scenario(plan):
    return {
        "data_class": "simulation_truth",
        "plan": plan,
        "effects": {
            "status": "passed",
            "episodes": [
                {
                    **i,
                    "data_class": "simulation_truth",
                    "affected_daily_grains": 1,
                    "baseline_latent_units": 3,
                    "injected_latent_units": 9,
                    "normal_inventory_outcome": {"observed_quantity": 0},
                    "injected_inventory_outcome": {"observed_quantity": 0},
                }
                for i in plan["injections"]
            ],
        },
    }


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    raw = path.read_bytes()
    return {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}


class TableSet(dict):
    pass


class ControlledReader:
    MAX_METADATA_BYTES = 2 * 1024**2

    def __init__(self, worlds):
        self.worlds = worlds
        self.reads = []
        self.ordinary_tables = None
        self.after_read = lambda path: None
        self.producer_changed = False

    def load_json(self, path):
        return json.loads(path.read_text())

    def safe_file(self, dataset, relative, *, limit=128 * 1024**2):
        path = dataset / relative
        assert path.is_file() and path.stat().st_size <= limit
        return path

    def verify_artifact(self, dataset, reference, name):
        path = dataset / name
        raw = path.read_bytes()
        if reference != {
            "path": name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }:
            raise ValueError("controlled_native_artifact_changed")
        return path

    def read_source_dataset(self, path):
        if self.ordinary_tables is not None:
            assert self.ordinary_tables() is None, "the first full world is still retained"
        self.reads.append(path)
        result = TableSet(copy.deepcopy(self.worlds[path]))
        self.ordinary_tables = weakref.ref(result)
        self.after_read(path)
        return result, self.load_json(path / "dataset_manifest.v2.json")


def fixture(tmp_path, monkeypatch):
    roots = [tmp_path / "ordinary", tmp_path / "planned"]
    values = [world(), world()]
    values[1]["orders"][0]["total"] = 11
    plan = {"injections": [injection()]}
    params = {"profile": "ai-smoke", "start_date": "2026-05-24", "end_date": "2026-05-26"}
    provenance = {
        "git_commit": "a" * 40,
        "code_state": "clean",
        "code_sha256": "b" * 64,
        "dependency_sha256": "c" * 64,
        "python_version": "3.11.15",
    }
    manifests = []
    for pos, path in enumerate(roots):
        path.mkdir()
        desc = {
            "resolved_parameters": params,
            "inventory_configuration_sha256": "e" * 64,
            "context": {"fixed": "same"},
            **{k: provenance[k] for k in ("code_sha256", "dependency_sha256", "python_version")},
            "tables": {k: {"row_count": len(v)} for k, v in values[pos].items()},
        }
        if pos:
            desc["scenario_plan_sha256"] = ordinary_worker._digest(plan)
        report = {
            "facts_ready": True,
            "status": "passed",
            "checks": [
                {"check_id": c, "status": "passed"}
                for c in sorted(worker.REQUIRED_CHECKS | (worker.ANOMALY_CHECKS if pos else set()))
            ],
        }
        manifest = {
            "schema_version": "2.8.0" if pos else "2.7.0",
            "dataset_id": "source-sha256-" + ordinary_worker._digest(desc),
            "descriptor": desc,
            "provenance": provenance,
            "facts_ready": True,
            "artifacts": {
                name: save(path / (name + ".json"), rows) for name, rows in values[pos].items()
            },
            "reports": {"source_report.json": save(path / "source_report.json", report)},
            "inventory_configuration": save(path / "configuration.json", {"same": True}),
        }
        if pos:
            manifest["scenario"] = save(path / "scenario.json", scenario(plan))
        save(path / "dataset_manifest.v2.json", manifest)
        manifests.append(manifest)
    io = ControlledReader(dict(zip(roots, values, strict=True)))
    monkeypatch.setattr(
        ordinary_worker,
        "_producer",
        lambda *_: (
            io,
            {**provenance, "code_state": "modified"} if io.producer_changed else provenance,
        ),
    )
    request = {
        "producer_commit": provenance["git_commit"],
        "producer_lock_sha256": provenance["dependency_sha256"],
        "exporter_lock_sha256": "f" * 64,
        "ordinary_source_dataset_id": manifests[0]["dataset_id"],
        "planned_source_dataset_id": manifests[1]["dataset_id"],
        "resolved_parameters": params,
        "scenario_plan": plan,
        "window": {"start": params["start_date"], "end": params["end_date"]},
        "max_source_bytes": 2 * 1024**3,
        "max_source_rows": 20000000,
    }
    output = tmp_path / "output"
    output.mkdir()
    return io, roots, output, request


def execute(case):
    io, roots, output, request = case
    return worker.verify_paired_sources(output, *roots, output, request)


def test_both_complete_readers_called_sequentially_before_paired_truth(
    tmp_path, monkeypatch, scalar_witness
):
    case = fixture(tmp_path, monkeypatch)
    verified, comparison, windows, episodes = execute(case)
    assert case[0].reads == case[1]
    assert verified["both_complete_sources_and_reports_replayed"]
    assert verified["ordinary"]["source_tables"] == verified["planned"]["source_tables"] == 58
    assert comparison["unknown_from_by_product"]["product-b"] == "2026-05-25"
    assert not any(
        w["product_id"] == "product-b" and w["window"]["end"] >= "2026-05-25" for w in windows
    )
    assert len(episodes) == 1
    assert verified["episodes_sha256"] == ordinary_worker._digest(episodes)
    assert verified["complete_windows_sha256"] == ordinary_worker._digest(windows)
    assert not verified["quality_qualified"]


@pytest.mark.parametrize(
    "field",
    [
        "ordinary_source_dataset_id",
        "planned_source_dataset_id",
        "scenario_plan",
        "resolved_parameters",
        "max_source_bytes",
        "max_source_rows",
    ],
)
def test_manifest_or_request_mismatch_fails_before_full_reader(tmp_path, monkeypatch, field):
    case = fixture(tmp_path, monkeypatch)
    case[3][field] = (
        {"different": True}
        if field in {"scenario_plan", "resolved_parameters"}
        else (1 if field.startswith("max_") else "source-sha256-" + "0" * 64)
    )
    with pytest.raises((ValueError, KeyError), match="manifest_binding|request_limits|start_date"):
        execute(case)
    assert case[0].reads == []


def test_pair_with_resealed_different_configuration_is_rejected_before_replay(
    tmp_path, monkeypatch
):
    case = fixture(tmp_path, monkeypatch)
    path = case[1][1] / "dataset_manifest.v2.json"
    doc = json.loads(path.read_text())
    doc["descriptor"]["inventory_configuration_sha256"] = "0" * 64
    doc["dataset_id"] = "source-sha256-" + ordinary_worker._digest(doc["descriptor"])
    save(path, doc)
    case[3]["planned_source_dataset_id"] = doc["dataset_id"]
    with pytest.raises(ValueError, match="parent_pair_mismatch"):
        execute(case)
    assert case[0].reads == []


@pytest.mark.parametrize("kind", ["missing", "failed", "duplicate"])
def test_all_native_anomaly_checks_required_even_with_resealed_report(
    tmp_path, monkeypatch, scalar_witness, kind
):
    case = fixture(tmp_path, monkeypatch)
    path = case[1][1]
    report = json.loads((path / "source_report.json").read_text())
    selected = next(c for c in report["checks"] if c["check_id"] == "anomaly_process_replay")
    if kind == "missing":
        report["checks"].remove(selected)
    elif kind == "failed":
        selected["status"] = "failed"
    else:
        report["checks"].append(selected)
    manifest = json.loads((path / "dataset_manifest.v2.json").read_text())
    manifest["reports"]["source_report.json"] = save(path / "source_report.json", report)
    save(path / "dataset_manifest.v2.json", manifest)
    with pytest.raises(ValueError, match="complete_report_replay"):
        execute(case)


@pytest.mark.parametrize("kind", ["artifact", "manifest", "extra_file", "producer"])
def test_change_during_second_parent_read_invalidates_whole_pair(
    tmp_path, monkeypatch, scalar_witness, kind
):
    case = fixture(tmp_path, monkeypatch)
    io, roots, _, _ = case

    def change(path):
        if path != roots[1]:
            return
        if kind == "producer":
            io.producer_changed = True
        elif kind == "artifact":
            (roots[0] / "products.json").write_text("changed")
        elif kind == "extra_file":
            (roots[0] / "extra").write_text("changed")
        else:
            path = roots[0] / "dataset_manifest.v2.json"
            save(path, {**json.loads(path.read_text()), "facts_ready": False})

    io.after_read = change
    with pytest.raises(
        ValueError, match="artifact_changed|source_changed|inventory_changed|producer_changed"
    ):
        execute(case)


def test_episode_clock_is_local_to_episode_and_keeps_censored_demand_positive(scalar_witness):
    plan = {"injections": [injection()]}
    evidence = worker.episode_evidence(world(), plan, {"start": "2026-05-24", "end": "2026-05-26"})
    result = worker.positive_episodes(evidence, evidence, scenario(plan))
    assert result[0]["first_evidence_available_at"] == "2026-05-27T00:00:00Z"
    assert result[0]["label_available_at"] == "2026-05-27T00:00:00Z"
    assert result[0]["business_type"] == "one_day_spike"


def test_return_episode_respects_native_ingestion_delay_not_only_72_hours(scalar_witness):
    plan = {"injections": [injection(injection_type="return_spike")]}
    evidence = worker.episode_evidence(world(), plan, {"start": "2026-05-24", "end": "2026-05-26"})
    assert evidence["episode"]["label_available_at"] == "2026-05-31T00:00:00Z"


def test_episode_crossing_requested_boundary_is_not_silently_clipped(scalar_witness):
    with pytest.raises(ValueError, match="episode_split_boundary"):
        worker.episode_evidence(
            world(),
            {"injections": [injection(end_date="2026-05-27")]},
            {"start": "2026-05-24", "end": "2026-05-26"},
        )


@pytest.mark.parametrize("kind", ["missing", "zero", "different", "duplicate"])
def test_unverified_or_relabelled_primary_effects_cannot_create_positives(scalar_witness, kind):
    plan = {"injections": [injection()]}
    evidence = worker.episode_evidence(world(), plan, {"start": "2026-05-24", "end": "2026-05-26"})
    actual = scenario(plan)
    effect = actual["effects"]["episodes"][0]
    if kind == "missing":
        actual["effects"]["episodes"].clear()
    elif kind == "duplicate":
        actual["effects"]["episodes"].append(effect)
    elif kind == "zero":
        effect["affected_daily_grains"] = 0
    else:
        effect["product_id"] = "other-product"
    with pytest.raises(ValueError, match="episode_inventory|primary_effect_binding"):
        worker.positive_episodes(evidence, evidence, actual)
