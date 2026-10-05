"""The serving smoke selects physical membership only and never reads outcome columns."""

import importlib.util
from pathlib import Path

import pyarrow as arrow
import pyarrow.parquet as parquet
import pytest
import test_stockout_final_campaign as campaign_fixtures

from retailops_ai.data_contracts.identity import canonical_bytes

recipes = campaign_fixtures.recipes
frozen = campaign_fixtures.frozen
path = Path(__file__).resolve().parents[1] / "scripts/qualify_stockout_final.py"
spec = importlib.util.spec_from_file_location("stockout_final_qualification_cli", path)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def test_smoke_scope_uses_membership_columns_and_keeps_physical_locations(
    tmp_path, frozen, monkeypatch
):
    freeze, _ = frozen
    origin = "2026-07-25T23:59:59Z"
    rows = [
        dict(
            product_id="fixture-sku-" + str(i),
            stock_location_id=stock,
            as_of=origin,
            role="test",
            eligible=i != 0,
            forbidden_outcome=999,
        )
        for i in range(25)
        for stock in freeze.expected_stock_locations
    ]
    parquet.write_table(arrow.Table.from_pylist(rows), tmp_path / "membership.parquet")
    (tmp_path / "manifest.json").write_bytes(
        canonical_bytes(
            dict(
                descriptor=dict(
                    partitions=[dict(files=[dict(role="membership", path="membership.parquet")])]
                ),
            )
        )
    )
    read = subject.parquet.read_table

    def bounded_read(*args, columns):
        assert "forbidden_outcome" not in columns
        assert set(columns) == {"product_id", "stock_location_id", "as_of", "role", "eligible"}
        return read(*args, columns=columns)

    monkeypatch.setattr(subject.parquet, "read_table", bounded_read)
    scope, as_of = subject.smoke_scope(tmp_path, freeze)
    assert len(scope.product_ids) == 20
    assert scope.stock_location_ids == freeze.expected_stock_locations
    assert as_of.isoformat() == "2026-07-25T23:59:59+00:00"


def test_no_eligible_final_membership_cannot_create_a_serving_smoke(tmp_path, frozen):
    parquet.write_table(
        arrow.Table.from_pylist(
            [
                dict(
                    product_id="fixture-sku",
                    stock_location_id=frozen[0].expected_stock_locations[0],
                    as_of="2026-07-25T23:59:59Z",
                    role="train",
                    eligible=True,
                )
            ]
        ),
        tmp_path / "membership.parquet",
    )
    (tmp_path / "manifest.json").write_bytes(
        canonical_bytes(
            dict(
                descriptor=dict(
                    partitions=[dict(files=[dict(role="membership", path="membership.parquet")])]
                ),
            )
        )
    )
    with pytest.raises(ValueError, match="no_eligible_membership"):
        subject.smoke_scope(tmp_path, frozen[0])
