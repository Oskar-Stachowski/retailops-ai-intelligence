"""Pinned remote preparation and verified archives, without generating heavy data."""

import importlib.util
import json
import shutil
import tarfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stockout_remote", ROOT / "scripts/prepare_stockout_remote.py"
)
assert SPEC and SPEC.loader
remote = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(remote)


@pytest.fixture
def pinned(monkeypatch, tmp_path):
    consumer, source = tmp_path / "consumer", tmp_path / "source"
    monkeypatch.setattr(
        remote,
        "head",
        lambda p: (
            remote.CONSUMER if p == consumer else remote.PRODUCER if p == source else "f" * 40
        ),
    )
    calls = []
    monkeypatch.setattr(remote.subprocess, "check_call", lambda argv, **kw: calls.append(argv))
    return consumer, source, calls


@pytest.mark.parametrize("seed", [42, 137, 2026])
def test_pinned_plan_never_evaluates_final_quality_and_develops_only_on42(pinned, seed):
    consumer, source, calls = pinned
    p = remote.plan(ROOT, consumer, source, seed)
    assert p["development_selection_permitted"] is (seed == 42)
    assert (
        not p["final_test_outcomes_evaluated"]
        and not p["promotion_permitted"]
        and not p["ai08_ready"]
    )
    assert p["consumer_commit"] == remote.CONSUMER and p["producer_commit"] == remote.PRODUCER
    assert len(calls) == 2 and all("--quiet" in c for c in calls)


@pytest.mark.parametrize("change", ["seed", "source_pin", "geometry", "budget", "baseline"])
def test_resealed_control_changes_are_rejected_before_generation(pinned, monkeypatch, change):
    consumer, source, calls = pinned
    real_read = remote.read

    def changed(path, cap=1024**2):
        d = deepcopy(real_read(path, cap))
        if "pilot" in path.name:
            if change == "seed":
                d["generation"]["seed"] = 137
            elif change == "source_pin":
                d["producer_commit"] = "0" * 40
            elif change == "geometry":
                d["generation"]["products"] = 31
            elif change == "budget":
                d["budgets"]["minimum_free_bytes"] = 1
            else:
                d["measured_resource_baseline"]["sha256"] = "1" * 64
        return d

    monkeypatch.setattr(remote, "read", changed)
    with pytest.raises(ValueError, match="frozen_profile"):
        remote.plan(ROOT, consumer, source, 42)
    assert not calls


@pytest.fixture
def parents(tmp_path, monkeypatch):
    retained = tmp_path / "retained"
    retained.mkdir()
    for name in (
        "facts_import",
        "private_import",
        "curated",
        "features",
        "upstream",
        "labels",
        "temporal",
    ):
        d = retained / name
        d.mkdir()
        (d / "content.bin").write_bytes((name + "\n").encode() * 10)
    (retained / "consumer.json").write_text(
        json.dumps(
            dict(
                paths={n: str(retained / n) for n in ("facts_import", "private_import", "curated")}
            )
        )
    )
    unrelated = retained / "producer-code"
    unrelated.mkdir()
    (unrelated / "excluded.txt").write_text("excluded code")
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(remote.shutil, "disk_usage", lambda p: SimpleNamespace(free=8 * 1024**3))
    return retained, out


def test_lossless_archive_contains_only_the_seven_consumer_parent_trees(parents):
    retained, out = parents
    mapping = remote.archive_inputs(retained, out)
    assert mapping["verified_lossless"] and not mapping["final_test_outcomes_evaluated"]
    assert len(mapping["files"]) == 7
    assert not any(n.startswith("producer-code") for n in mapping["files"])
    with tarfile.open(out / mapping["archive_file"], "r:gz") as tar:
        for m in tar.getmembers():
            assert m.isfile() and tar.extractfile(m).read() == (retained / m.name).read_bytes()
    assert remote.sha(out / mapping["archive_file"]) == mapping["archive_sha256"]
    assert all((retained / n).is_dir() for n in mapping["parent_roots"])


def test_symlink_parent_is_refused_without_a_published_archive(parents):
    retained, out = parents
    (retained / "features/link").symlink_to(retained / "private_import/content.bin")
    with pytest.raises(ValueError, match="symlink"):
        remote.archive_inputs(retained, out)
    assert not (out / "parents.tar.gz").exists()


def test_missing_parent_is_refused_without_a_published_archive(parents):
    retained, out = parents
    shutil.rmtree(retained / "upstream")
    with pytest.raises(ValueError, match="missing_parent"):
        remote.archive_inputs(retained, out)
    assert not (out / "parents.tar.gz").exists()


def test_archive_disk_limit_preserves_sources_and_removes_only_its_staging(parents, monkeypatch):
    retained, out = parents
    monkeypatch.setattr(
        remote.shutil, "disk_usage", lambda p: SimpleNamespace(free=remote.RESERVE - 1)
    )
    with pytest.raises(ValueError, match="resource_limit"):
        remote.archive_inputs(retained, out)
    assert not list(out.iterdir())
    assert (retained / "features/content.bin").exists()


def test_archive_input_cap_prevents_publishing_oversized_data(parents, monkeypatch):
    retained, out = parents
    monkeypatch.setattr(remote, "MAX_ARCHIVE", 1)
    with pytest.raises(ValueError, match="input_limit"):
        remote.archive_inputs(retained, out)
    assert not list(out.iterdir())


def test_checkpoint_write_is_immutable_and_bounded_control_reads_reject_symlinks(tmp_path):
    p = tmp_path / "checkpoint.json"
    remote.write(p, dict(final_test_outcomes_evaluated=False))
    with pytest.raises(FileExistsError):
        remote.write(p, {})
    alias = tmp_path / "alias.json"
    alias.symlink_to(p)
    with pytest.raises(ValueError):
        remote.read(alias)
    with pytest.raises(ValueError):
        remote.read(p, 1)
