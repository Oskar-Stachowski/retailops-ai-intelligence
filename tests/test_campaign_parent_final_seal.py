"""One final full-parent pass, retaining corruption refusal before receipts."""

import shutil

import pytest
from test_campaign_anomaly_parent import public_parent as public_parent  # noqa: F401
from test_campaign_anomaly_parent import reader as anomaly_reader
from test_campaign_stockout_parent import reader as stockout_reader

from retailops_ai.evaluation_campaign import source_replay
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.fixture(params=["anomaly", "stockout"])
def kind(request):
    return request.param


def reader(kind, case, scratch):
    return (anomaly_reader if kind == "anomaly" else stockout_reader)(case, scratch)


def consume(kind, adapter):
    if kind == "anomaly":
        for event in adapter.parent.events:
            adapter.parent.match(event)
    else:
        for _ in adapter.rows():
            pass


def parents(case, adapter):
    return {
        "original_snapshot": case[0],
        "original_curated": case[1],
        "private_snapshot": adapter._replay.curated.parent / "snapshot",
        "private_curated": adapter._replay.curated,
    }


def test_single_final_pass_still_reads_every_complete_parent(
    public_parent, tmp_path, monkeypatch, kind
):
    sealed = []
    original = source_replay._seal_parent

    def observed(root, names, maximum):
        sealed.append(root)
        return original(root, names, maximum)

    monkeypatch.setattr(source_replay, "_seal_parent", observed)
    adapter = reader(kind, public_parent, tmp_path)
    with adapter:
        consume(kind, adapter)
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
        expected = set(parents(public_parent, adapter).values())
        sealed.clear()
    assert len(sealed) == 4 and set(sealed) == expected
    assert adapter.receipt()[
        "physical_source_and_curated_replay_passed"
        if kind == "anomaly"
        else "public_source_and_curated_replayed"
    ]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "parent", ["original_snapshot", "original_curated", "private_snapshot", "private_curated"]
)
def test_any_parent_changed_after_consumption_still_refuses_receipt(
    public_parent, tmp_path, kind, parent
):
    snapshot, curated, scratch = (tmp_path / name for name in ("snapshot", "curated", "scratch"))
    shutil.copytree(public_parent[0], snapshot)
    shutil.copytree(public_parent[1], curated)
    scratch.mkdir()
    case = snapshot, curated, *public_parent[2:]
    adapter = reader(kind, case, scratch)
    with pytest.raises(SnapshotError, match="parent_changed"):
        with adapter:
            consume(kind, adapter)
            root = parents(case, adapter)[parent]
            target = next(root.rglob("*.parquet"))
            target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(scratch.iterdir())
