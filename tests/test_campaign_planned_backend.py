"""Producer-pinned backend routing only; native Source parity has separate evidence."""

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
