"""The existing native replay and mapping queries use bounded disk indexes."""

from retailops_ai.curated.transform import Index
from retailops_ai.source_snapshot.inventory_projection import Facts


def test_native_delivery_plan_query_uses_order_and_version_index(tmp_path):
    facts = Facts(tmp_path / "facts.sqlite")
    try:
        rows = facts.db.execute(
            "EXPLAIN QUERY PLAN SELECT row FROM facts WHERE name='delivery_plan_versions' AND json_extract(row,'$.replenishment_order_id')=? ORDER BY json_extract(row,'$.version')",
            ("order",),
        ).fetchall()
        assert any("delivery_plan_order" in row[3] for row in rows)
        assert not any("TEMP B-TREE" in row[3] for row in rows)
    finally:
        facts.db.close()


def test_curated_interval_and_legacy_queries_use_matching_indexes(tmp_path):
    index = Index(tmp_path / "mapping.sqlite")
    try:
        interval = index.db.execute(
            "EXPLAIN QUERY PLAN SELECT body FROM intervals WHERE kind=? AND location=? AND channel=? AND product=? AND start<=? AND end>? AND available<=? ORDER BY version DESC LIMIT 1025",
            (
                "assortment",
                "location",
                "store",
                "product",
                "2026-07-01",
                "2026-07-01",
                "2026-07-01",
            ),
        ).fetchall()
        assert any("interval_lookup" in row[3] and "product=?" in row[3] for row in interval)
        legacy = index.db.execute(
            "EXPLAIN QUERY PLAN SELECT DISTINCT location FROM intervals WHERE kind='channel_assignments' AND legacy=? AND channel=? AND start<=? AND end>? AND available<=?",
            ("legacy", "store", "2026-07-01", "2026-07-01", "2026-07-01"),
        ).fetchall()
        assert any("legacy_assignment_lookup" in row[3] for row in legacy)
    finally:
        index.close()
