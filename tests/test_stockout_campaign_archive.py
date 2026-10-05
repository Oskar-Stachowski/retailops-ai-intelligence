"""Adversarial archive mechanics; these tiny files are not qualified stockout sources."""

import hashlib
import io
import tarfile
from copy import deepcopy
from types import SimpleNamespace

import pytest

from retailops_ai.stockout_campaign import archive as subject


def package(tmp_path, *, mutation=None):
    roots = dict(
        facts_import="facts_import/source",
        private_import="private_import/source",
        curated="curated/source",
    )
    refs, entries = {}, []
    for role in sorted(subject.ROLES):
        name = roots.get(role, role) + (
            "/snapshot/snapshot_manifest.json"
            if role in {"facts_import", "private_import"}
            else "/manifest.json"
        )
        raw = b'{"fixture":"mechanics_only"}\n'
        member = tarfile.TarInfo(name)
        member.mode, member.size = 0o600, len(raw)
        refs[name] = dict(bytes=len(raw), mode=0o600, sha256=hashlib.sha256(raw).hexdigest())
        entries.append((member, raw))
    if mutation:
        mutation(entries)
    path = tmp_path / "parents.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for entry, raw in entries:
            tar.addfile(entry, io.BytesIO(raw) if entry.isfile() else None)
    checkpoint = dict(
        schema_version="stockout-future-source-checkpoint-1.0.0",
        final_test_outcomes_evaluated=False,
        independent_quality_accepted=False,
        model_promoted=False,
        ai08_ready=False,
        parents=dict(
            archive_file=path.name,
            archive_bytes=path.stat().st_size,
            archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            unpacked_bytes=sum(r["bytes"] for r in refs.values()),
            files=refs,
            parent_roots=roots,
            verified_lossless=True,
            final_test_outcomes_evaluated=False,
        ),
    )
    return path, checkpoint


@pytest.fixture(autouse=True)
def bounded_fixture_disk(monkeypatch):
    monkeypatch.setattr(subject.shutil, "disk_usage", lambda _: SimpleNamespace(free=120 * 1024**3))


def test_atomic_exact_restore_then_unchanged_reuse_and_changed_existing_refused(tmp_path):
    path, checkpoint = package(tmp_path)
    target = tmp_path / "restored"
    roots = subject.restore(path, checkpoint, target)
    assert set(roots) == subject.ROLES
    assert roots["private_import"] == target / "private_import/source"
    assert subject.partition_inputs(roots).private == roots["private_import"] / "snapshot"
    assert (subject.partition_inputs(roots).private / "snapshot_manifest.json").is_file()
    assert subject.restore(path, checkpoint, target) == roots
    (roots["features"] / "manifest.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="file_changed"):
        subject.restore(path, checkpoint, target)


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate",
        "missing",
        "extra",
        "traversal",
        "symlink",
        "hardlink",
        "directory",
        "mode",
        "content",
    ],
)
def test_bad_tar_never_publishes_a_partial_tree(tmp_path, kind):
    def mutate(entries):
        member, raw = entries[0]
        if kind == "duplicate":
            entries.append((member, raw))
        elif kind == "missing":
            entries.pop()
        elif kind == "extra":
            entry = tarfile.TarInfo("features/extra.json")
            entry.mode = 0o600
            entry.size = 2
            entries.append((entry, b"{}"))
        elif kind == "traversal":
            member.name = "../outside.json"
        elif kind in {"symlink", "hardlink", "directory"}:
            member.type = {
                "symlink": tarfile.SYMTYPE,
                "hardlink": tarfile.LNKTYPE,
                "directory": tarfile.DIRTYPE,
            }[kind]
            member.linkname = "../outside"
            member.size = 0
        elif kind == "mode":
            member.mode = 0o755
        else:
            entries[0] = (member, raw.replace(b"mechanics", b"corrupted"))

    path, checkpoint = package(tmp_path, mutation=mutate)
    target = tmp_path / "restored"
    with pytest.raises(ValueError):
        subject.restore(path, checkpoint, target)
    assert not target.exists() and not list(tmp_path.glob(".stockout-restore-*"))
    assert not (tmp_path.parent / "outside.json").exists()


@pytest.mark.parametrize(
    "change",
    ["bytes", "digest", "receipt", "final_access", "roles", "root", "bool_size", "executable"],
)
def test_untrusted_checkpoint_or_archive_cannot_be_reinterpreted(tmp_path, change):
    path, original = package(tmp_path)
    checkpoint = deepcopy(original)
    if change == "bytes":
        checkpoint["parents"]["archive_bytes"] += 1
    elif change == "digest":
        checkpoint["parents"]["archive_sha256"] = "f" * 64
    elif change == "receipt":
        checkpoint["parents"]["verified_lossless"] = 1
    elif change == "final_access":
        checkpoint["final_test_outcomes_evaluated"] = True
    elif change == "roles":
        del checkpoint["parents"]["files"]["features/manifest.json"]
    elif change == "root":
        checkpoint["parents"]["parent_roots"]["curated"] = "../outside"
    elif change == "bool_size":
        checkpoint["parents"]["files"]["features/manifest.json"]["bytes"] = True
    else:
        checkpoint["parents"]["files"]["features/manifest.json"]["mode"] = 0o755
    with pytest.raises(ValueError):
        subject.restore(path, checkpoint, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_disk_reserve_is_checked_before_any_unpack_and_local_policy_stays_50gib(
    tmp_path, monkeypatch
):
    path, checkpoint = package(tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert subject.reserve() == 50 * 1024**3
    monkeypatch.setattr(
        subject.shutil, "disk_usage", lambda _: SimpleNamespace(free=subject.reserve())
    )
    with pytest.raises(ValueError, match="free_disk_reserve"):
        subject.restore(path, checkpoint, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
