"""Producer-pinned backend routing only; native Source parity has separate evidence."""

import hashlib
from types import SimpleNamespace

import pytest

from retailops_ai.evaluation_campaign import campaign_generation_worker as worker


def test_old_pinned_source_keeps_ordinary_backend(tmp_path, monkeypatch):
    calls = []
    process = SimpleNamespace(build_tables=object())

    def load(name):
        calls.append(name)
        return process

    monkeypatch.setattr(worker.importlib, "import_module", load)
    actual, metadata = worker.planned_backend(tmp_path)
    assert actual is process
    assert calls == ["data.anomalies.source_process"]
    assert metadata == {"version": "ordinary_planned_source_2_8", "cached_execution": False}


def test_new_pinned_addon_records_actual_implementation(tmp_path, monkeypatch):
    addon = tmp_path / "data/anomalies/source_cohort.py"
    addon.parent.mkdir(parents=True)
    addon.write_text("# controlled addon presence; no code executed here\n")
    expected = {"version": "planned-source-cached-execution-1.0.0", "code_sha256": "a" * 64}
    calls = []
    process = SimpleNamespace(implementation=lambda: dict(expected), build_tables=object())

    def load(name):
        calls.append(name)
        return process

    monkeypatch.setattr(worker.importlib, "import_module", load)
    actual, metadata = worker.planned_backend(tmp_path)
    assert actual is process and metadata == expected
    assert calls == ["data.anomalies.source_cohort"]


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_invalid_addon_is_rejected_without_fallback_or_import(tmp_path, monkeypatch, kind):
    addon = tmp_path / "data/anomalies/source_cohort.py"
    addon.parent.mkdir(parents=True)
    if kind == "symlink":
        target = tmp_path / "outside.py"
        target.write_text("# not a producer-owned module\n")
        addon.symlink_to(target)
    else:
        addon.mkdir()

    def no_import(name):
        raise AssertionError("Invalid addon must fail before importing " + name)

    monkeypatch.setattr(worker.importlib, "import_module", no_import)
    with pytest.raises(ValueError, match="invalid_planned_backend"):
        worker.planned_backend(tmp_path)


def test_addon_pin_failure_is_not_silently_replaced_by_ordinary_backend(tmp_path, monkeypatch):
    addon = tmp_path / "data/anomalies/source_cohort.py"
    addon.parent.mkdir(parents=True)
    addon.write_text("# controlled addon presence\n")
    calls = []

    def failed_pins():
        raise ValueError("Planned cached source upstream changed; review parity first.")

    def load(name):
        calls.append(name)
        return SimpleNamespace(implementation=failed_pins)

    monkeypatch.setattr(worker.importlib, "import_module", load)
    with pytest.raises(ValueError, match="upstream changed"):
        worker.planned_backend(tmp_path)
    assert calls == ["data.anomalies.source_cohort"]


@pytest.mark.parametrize(
    ("version", "expected_options"),
    [
        ("ordinary_planned_source_2_8", {}),
        ("planned-source-cached-execution-1.0.0", {}),
        ("planned-source-cached-execution-1.1.0", {"consume_input": True}),
        ("planned-source-cached-execution-1.1.1", {"consume_input": True}),
        ("planned-source-cached-execution-1.1.2", {"consume_input": True}),
        ("planned-source-cached-execution-1.1.3", {"consume_input": True}),
    ],
)
def test_planned_producer_transfers_owned_tables_only_to_supported_writer(
    tmp_path, monkeypatch, version, expected_options
):
    source, root = tmp_path / "source", tmp_path / "run"
    for relative in ("data/requirements-parquet.txt", "services/api/requirements.txt"):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    parameters = {"profile": "ai-smoke", "seed": 42}
    provenance = {"git_commit": "a" * 40, "code_state": "clean", "dependency_sha256": "b" * 64}
    scenario_plan, config, context = object(), object(), object()
    owned_tables = {"orders": [{"order_id": "one"}]}
    backend = {"version": version}
    directory = root / "raw" / "validated"
    events = []

    def build(generation, plan, inventory_config):
        assert vars(generation) == parameters
        assert plan is scenario_plan and inventory_config is config
        events.append("build")
        return owned_tables, context

    def publish(tables, actual_context, generation, inventory_config, destination, **options):
        assert tables is owned_tables and actual_context is context
        assert inventory_config is config and destination == root / "raw"
        assert options.pop("scenario_plan") is scenario_plan
        assert options == expected_options
        if options.get("consume_input"):
            tables.clear()
        events.append("write")
        return directory

    def independent_read(path):
        assert path == directory and events == ["build", "write"]
        assert bool(owned_tables) is (not bool(expected_options))
        events.append("read")
        return {}, {
            "descriptor": {"resolved_parameters": parameters},
            "schema_version": "2.8.0",
            "provenance": provenance,
            "facts_ready": True,
            "dataset_id": "verified-dataset",
        }

    modules = {
        "data.inventory.source_dataset_io": SimpleNamespace(
            fingerprint=lambda: {},
            write_source_dataset=publish,
            read_source_dataset=independent_read,
        ),
        "data.generator.identity": SimpleNamespace(code_provenance=lambda _: provenance),
        "data.generator.configuration": SimpleNamespace(
            DatasetGenerationConfig=SimpleNamespace,
            resolve_generation_config=lambda _: SimpleNamespace(parameters=lambda: parameters),
        ),
        "data.inventory.run_source_dataset": SimpleNamespace(
            default_inventory_config=lambda _: config
        ),
    }
    monkeypatch.setattr(worker.importlib, "import_module", modules.__getitem__)
    monkeypatch.setattr(
        worker, "planned_backend", lambda _: (SimpleNamespace(build_tables=build), backend)
    )
    monkeypatch.setattr(worker.sys, "path", list(worker.sys.path))
    result = worker.producer(
        "generation",
        source,
        root,
        {
            "source": {"producer_commit": "a" * 40, "producer_lock_sha256": "b" * 64},
            "plan": {
                "entrypoint": "planned_anomaly",
                "exporter_lock_sha256": hashlib.sha256(b"").hexdigest(),
                "requested_parameters": parameters,
                "resolved_parameters": parameters,
                "scenario_plan": scenario_plan,
            },
        },
    )
    assert events == ["build", "write", "read"]
    assert result == {
        "directory": str(directory),
        "source_dataset_id": "verified-dataset",
        "producer_execution": backend,
    }
