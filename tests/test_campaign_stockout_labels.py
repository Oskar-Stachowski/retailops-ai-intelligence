"""Full label replay on exposed Source 1.1/1.2 controls, no new Project reads."""

import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile

import pytest
from test_campaign_anomaly_parent import public_parent as public_parent  # noqa: F401
from test_campaign_anomaly_parent import source

from retailops_ai.evaluation_campaign import source_replay
from retailops_ai.evaluation_campaign.campaign_stockout_label_facts import (
    CampaignStockoutLabelStoragePolicy,
)
from retailops_ai.evaluation_campaign.campaign_stockout_labels import (
    CampaignStockoutLabelPlan,
    CampaignStockoutLabels,
)
from retailops_ai.evaluation_campaign.campaign_stockout_qualification import NAMES, WINDOWS
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.source_snapshot.files import SnapshotError, json_sha256
from retailops_ai.source_snapshot.inventory_projection import rows
from retailops_ai.source_snapshot.protocol import Limits, inspect_snapshot
from retailops_ai.stockout.contract import DEFAULT_POLICY, LedgerMovement
from retailops_ai.stockout_label_partitions.bundle import DEFAULT_PARTITION_POLICY, LabelPreparation
from retailops_ai.stockout_label_partitions.store import TABLES, LabelFacts


@pytest.fixture(scope="module")
def qualified(public_parent, tmp_path_factory):
    parent = inspect_snapshot(public_parent[0], False, Limits())
    target = tmp_path_factory.mktemp("ai09-label-original-qualification")
    found = False
    for archive_name, prefixes in (
        ("inventory-v1_1.zip", ["private"]),
        ("anomaly-v1_2.zip", ["demand/private", "physical/private"]),
    ):
        with ZipFile(Path(__file__).parents[1] / "data/fixtures" / archive_name) as archive:
            for prefix in prefixes:
                document = json.loads(archive.read(prefix + "/snapshot_manifest.json"))
                if document["source_dataset_id"] != parent.source_id:
                    continue
                assert not found
                found = True
                for name in NAMES:
                    path = target / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(
                        archive.read(prefix + "/evaluation_truth/qualification/" + name)
                    )
    assert found
    return target, parent


def reader(public_parent, qualified, scratch, **updates):
    plan = CampaignStockoutLabelPlan(
        **{
            "source": source(*public_parent[:2]),
            "runtime": runtime_pin(),
            **updates,
        }
    )
    return CampaignStockoutLabels(
        *public_parent[:2],
        qualified[0],
        plan,
        scratch,
        allow_evaluation_truth=True,
    )


def reference(public_parent, qualified):
    """Independent in-memory fixture lookup, native point projection unchanged."""
    parent = qualified[1]
    tables = {t["table"]: t for t in parent.manifest["tables"]}
    original = {name: list(rows(public_parent[0], tables[name], Limits())) for name in TABLES}
    ledger = defaultdict(list)
    for row in original["inventory_ledger"]:
        ledger[row["product_id"], row["stock_location_id"]].append(
            LedgerMovement.model_validate(
                {
                    **{
                        k: row[k]
                        for k in (
                            "product_id",
                            "stock_location_id",
                            "occurred_at",
                            "available_at",
                            "sequence",
                            "quantity_delta",
                            "movement_type",
                        )
                    },
                    "event_id": row["inventory_event_id"],
                }
            )
        )
    snapshots = {
        (r["product_id"], r["stock_location_id"], r["snapshot_at"]): r
        for r in original["inventory_daily_snapshots"]
    }
    coverage = {r["coverage_id"]: r for r in original["inventory_history_coverage"]}

    class ReferenceFacts(LabelFacts):
        def __init__(self):
            self.snapshot = parent
            self.label_policy = DEFAULT_POLICY

        def get(self, table, key):
            assert table == "inventory_history_coverage"
            return coverage.get(key)

        def snapshot_at(self, product, stock, origin):
            return snapshots.get((product, stock, origin))

    projection = LabelPreparation(ReferenceFacts(), DEFAULT_PARTITION_POLICY)
    return [
        projection.point(
            window, ledger[window["product_id"], window["stock_location_id"]]
        ).model_dump(mode="json")
        for window in json.loads((qualified[0] / WINDOWS).read_bytes())
    ]


def test_all_original_labels_exact_native_field_and_complete_origin_parity(
    public_parent, qualified, tmp_path, monkeypatch
):
    seals = []
    original_seal = source_replay._seal_parent

    def seal(directory, names, maximum):
        seals.append(directory)
        return original_seal(directory, names, maximum)

    monkeypatch.setattr(source_replay, "_seal_parent", seal)
    expected = reference(public_parent, qualified)
    adapter = reader(public_parent, qualified, tmp_path)
    with adapter:
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
        actual = list(adapter.rows())
        assert actual == expected
        assert adapter._facts.policy == adapter.plan.storage
        assert adapter._facts.policy.projection == "closed_ai08_label_preparation_point_unchanged"
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
        # Count actual whole-parent reads after complete consumption. Final
        # receipt acceptance needs exactly one pass over all four parents.
        parents = {
            public_parent[0],
            public_parent[1],
            adapter._replay.curated,
            adapter._replay.curated.parent / "snapshot",
        }
        seals.clear()
    assert len(seals) == 4 and set(seals) == parents
    receipt = adapter.receipt()
    assert receipt["rows"] == len(expected)
    assert receipt["points_sha256"] == json_sha256(expected)
    assert receipt["ledger_replay_verified"] and receipt["complete_physical_origin_join_verified"]
    assert receipt["original_qualification"]["rows"] == len(expected)
    assert (
        receipt["label_facts_seal"]["storage_policy_sha256"]
        == adapter.plan.storage.content_sha256()
    )
    assert not receipt["journal_authorization_proven"] and not receipt["stage_ready"]
    receipt["rows"] = -1
    assert adapter.receipt()["rows"] == len(expected)
    with pytest.raises(SnapshotError, match="single_use"):
        adapter.__enter__()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("consumption", ["none", "one", "repeated_caught"])
def test_partial_or_caught_repeated_read_never_produces_receipt(
    public_parent, qualified, tmp_path, consumption
):
    adapter = reader(public_parent, qualified, tmp_path)
    with pytest.raises(SnapshotError, match="complete_stream"):
        with adapter:
            if consumption != "none":
                iterator = adapter.rows()
                next(iterator)
            if consumption == "repeated_caught":
                with pytest.raises(SnapshotError, match="repeated_stream"):
                    list(adapter.rows())
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    if consumption != "none":
        with pytest.raises((SnapshotError, ValueError)):
            next(iterator)
    assert not list(tmp_path.iterdir())


def test_explicit_private_opt_in_precedes_any_io(public_parent, qualified, tmp_path):
    adapter = reader(public_parent, qualified, tmp_path)
    adapter.snapshot = tmp_path / "missing"
    adapter.allow_truth = False
    with pytest.raises(SnapshotError, match="private_opt_in"):
        adapter.__enter__()


@pytest.mark.parametrize("budget", ["selected", "index", "windows"])
def test_explicit_limits_refuse_instead_of_truncating(public_parent, qualified, tmp_path, budget):
    updates = {}
    if budget == "windows":
        from retailops_ai.evaluation_campaign.campaign_stockout_qualification import (
            CampaignStockoutQualificationPolicy,
        )

        updates["qualification"] = CampaignStockoutQualificationPolicy(max_windows=1)
    else:
        updates["storage"] = CampaignStockoutLabelStoragePolicy(
            **{
                "max_selected_rows" if budget == "selected" else "max_index_bytes": 1
                if budget == "selected"
                else 4096,
            }
        )
    adapter = reader(public_parent, qualified, tmp_path, **updates)
    with pytest.raises(SnapshotError):
        with adapter:
            list(adapter.rows())
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "mutation", ["missing_origin", "extra_origin", "ledger", "coverage", "snapshot_identity"]
)
def test_private_fact_mutation_and_in_memory_parent_mutation_are_refused(
    public_parent, qualified, tmp_path, mutation
):
    adapter = reader(public_parent, qualified, tmp_path)
    with pytest.raises((SnapshotError, ValueError)):
        with adapter:
            if mutation == "snapshot_identity":
                list(adapter.rows())
                adapter._native.snapshot.manifest["snapshot_id"] = "snapshot-sha256-" + "0" * 64
            else:
                db = adapter._facts._connection()
                db.execute("PRAGMA query_only=OFF")
                if mutation == "extra_origin":
                    db.execute(
                        "INSERT INTO facts SELECT table_name,position+100000,product,stock,ready,payload,checksum FROM facts WHERE table_name='inventory_daily_snapshots' AND position=0"
                    )
                else:
                    table = {
                        "missing_origin": "inventory_daily_snapshots",
                        "ledger": "inventory_ledger",
                        "coverage": "inventory_history_coverage",
                    }[mutation]
                    db.execute("DELETE FROM facts WHERE table_name=? AND position=0", (table,))
                db.commit()
                db.execute("PRAGMA query_only=ON")
                list(adapter.rows())
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(tmp_path.iterdir())


def test_original_sidecar_changed_after_consumption_invalidates_receipt(
    public_parent, qualified, tmp_path
):
    root, scratch = tmp_path / "sidecar", tmp_path / "scratch"
    shutil.copytree(qualified[0], root)
    scratch.mkdir()
    adapter = reader(public_parent, (root, qualified[1]), scratch)
    with pytest.raises(SnapshotError, match="parent_changed"):
        with adapter:
            list(adapter.rows())
            path = root / "qualification_report.json"
            path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(scratch.iterdir())


@pytest.mark.parametrize(
    "parent", ["original_snapshot", "original_curated", "private_snapshot", "private_curated"]
)
def test_unused_parent_bytes_changed_after_consumption_invalidate_receipt(
    public_parent, qualified, tmp_path, parent
):
    snapshot, curated, scratch = (tmp_path / name for name in ("snapshot", "curated", "scratch"))
    shutil.copytree(public_parent[0], snapshot)
    shutil.copytree(public_parent[1], curated)
    scratch.mkdir()
    adapter = reader((snapshot, curated, *public_parent[2:]), qualified, scratch)
    with pytest.raises(SnapshotError, match="parent_changed"):
        with adapter:
            list(adapter.rows())
            parents = {
                "original_snapshot": snapshot,
                "original_curated": curated,
                "private_snapshot": adapter._replay.curated.parent / "snapshot",
                "private_curated": adapter._replay.curated,
            }
            # Stockout labels do not query sale facts. The final seal must
            # nevertheless detect their corruption after the last yielded row.
            target = next(p for p in parents[parent].rglob("*.parquet") if "sales" in str(p))
            target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(scratch.iterdir())


def test_physical_ledger_query_does_not_apply_old_global_100000_row_limit(tmp_path, monkeypatch):
    """Synthetic query isolation only, explicitly not Source or capacity proof."""
    import sqlite3

    from retailops_ai.curated.contract import encoded
    from retailops_ai.evaluation_campaign.campaign_stockout_label_facts import (
        CampaignStockoutLabelFacts,
        CampaignStockoutLabelStore,
    )
    from retailops_ai.source_snapshot.protocol import Snapshot

    # Use the real sealed-store query method against a separately constructed
    # unit database. This deliberately does not bypass a production entry point.
    owner = CampaignStockoutLabelStore.__new__(CampaignStockoutLabelStore)
    owner.curated = tmp_path
    owner.policy = CampaignStockoutLabelStoragePolicy()
    owner.document = {"unit_query_only": True}
    owner._failed = False
    owner.stats = {"maximum_selected_rows": 0, "maximum_selected_bytes": 0}
    owner._columns = {
        "inventory_ledger": [
            {"name": name, "type": "timestamp[us, tz=UTC]"}
            for name in ("occurred_at", "available_at")
        ]
    }
    manifest = {
        "source": {
            "descriptor": {
                "context": {
                    "projection": {
                        "stock_measure": "available_qty",
                        "reservation_policy": "none",
                        "episode_policy": "zero_after_event_including_instantaneous",
                        "diagnostic_horizon_days": 7,
                    }
                }
            }
        }
    }
    snapshot = Snapshot(manifest, (), "0" * 64)
    with sqlite3.connect(tmp_path / "unit.sqlite") as db:
        owner._db = db
        db.execute("PRAGMA cache_size=-8192")
        db.execute(
            "CREATE TABLE facts(table_name TEXT,position INTEGER,product TEXT,stock TEXT,ready TEXT,payload BLOB,checksum TEXT)"
        )
        db.execute("CREATE INDEX physical ON facts(table_name,product,stock,ready,position)")

        def facts():
            for position in range(100003):
                product = "selected" if position < 2 else "unrelated"
                row = dict(
                    product_id=product,
                    stock_location_id="stock",
                    inventory_event_id=f"event-{position}",
                    occurred_at="2026-01-01T00:00:00+00:00",
                    available_at="2026-01-01T00:00:00+00:00",
                    sequence=position,
                    quantity_delta=1,
                    movement_type="opening_stock" if position in (0, 2) else "inventory_adjustment",
                )
                raw = encoded(row)
                yield (
                    "inventory_ledger",
                    position,
                    product,
                    "stock",
                    "",
                    raw,
                    hashlib.sha256(raw).hexdigest(),
                )

        db.executemany("INSERT INTO facts VALUES(?,?,?,?,?,?,?)", facts())
        db.commit()
        projection = CampaignStockoutLabelFacts(owner, snapshot)
        assert db.execute("SELECT count(*) FROM facts").fetchone()[0] == 100003
        assert [m.event_id for m in projection.ledger("selected", "stock")] == [
            "event-0",
            "event-1",
        ]
        assert owner.stats["maximum_selected_rows"] == 2
        assert owner.stats["maximum_selected_bytes"] > 0
        # The native per-physical-series ceiling remains enforced even when the
        # outer AI09 selected-row allowance is larger.
        owner.policy = CampaignStockoutLabelStoragePolicy(max_selected_rows=200000)
        monkeypatch.setattr(
            owner, "_selected", lambda *a, **k: pytest.fail("oversized physical ledger decoded")
        )
        with pytest.raises(SnapshotError, match="selected_resource_limit"):
            projection.ledger("unrelated", "stock")
    owner._db = None
