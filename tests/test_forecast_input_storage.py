"""Compressed temporary indexing preserves canonical content and enforces budgets."""

import hashlib
import json
import shutil
import sqlite3
import zlib
from collections import Counter

import pytest
from test_forecast_features import ORIGIN, SERIES
from test_forecast_features import real_inputs as real_inputs
from test_forecast_features import tables as tables

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting import features_store
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.source_snapshot.files import SnapshotError


def index():
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE output (artifact TEXT, key BLOB, body BLOB, PRIMARY KEY(artifact,key))"
    )
    return db


def test_compressed_index_preserves_all_canonical_content_and_sorted_identity(tables, tmp_path):
    view = OriginFeatures(tables, ORIGIN)
    rows = list(view.targets(view.history(SERIES)))
    with index() as db:
        writer = features_store.Writer(tmp_path, "features", db, Counter())
        for row in reversed(rows):
            writer.add(row)
        canonical = sorted(
            (
                canonical_bytes(
                    [row.model_dump(mode="json")[k] for k in (*features_store.KEYS, "target_date")]
                ),
                canonical_bytes(row.model_dump(mode="json")),
            )
            for row in rows
        )
        expected = hashlib.sha256(b"".join(body + b"\n" for _, body in canonical)).hexdigest()
        actual = [
            (key, zlib.decompress(body))
            for key, body in db.execute("SELECT key,body FROM output ORDER BY key")
        ]
        assert actual == canonical
        assert writer.summary()["content_sha256"] == expected
        assert (
            db.execute("SELECT sum(length(body)) FROM output").fetchone()[0]
            < sum(len(body) for _, body in canonical) / 2
        )
        with pytest.raises(SnapshotError, match="duplicate_forecast_input_key"):
            writer.add(rows[0])


@pytest.mark.parametrize(
    "limit,reason",
    [("MAX_LOGICAL_OUTPUT_BYTES", "logical_byte_limit"), ("MAX_OUTPUT_BYTES", "index_byte_limit")],
)
def test_compression_cannot_bypass_logical_or_physical_index_budgets(
    tables, tmp_path, monkeypatch, limit, reason
):
    view = OriginFeatures(tables, ORIGIN)
    row = next(iter(view.targets(view.history(SERIES))))
    monkeypatch.setattr(features_store, limit, 1)
    with index() as db:
        writer = features_store.Writer(tmp_path, "features", db, Counter())
        with pytest.raises(SnapshotError, match=reason):
            writer.add(row)


def test_verifier_uses_compressed_index_but_preserves_historical_input_contracts(
    real_inputs, tmp_path, monkeypatch
):
    _, _, directory = real_inputs
    current = features_store.verify_inputs(directory)
    assert current["descriptor"]["implementation"]["version"] == "forecast-inputs-1.2.0"
    assert current["descriptor"]["stats"]["logical_output_bytes"] > 0
    for version, limit in [
        ("forecast-inputs-1.0.0", 2 * 1024**3),
        ("forecast-inputs-1.1.0", 4 * 1024**3),
    ]:
        target = tmp_path / version
        shutil.copytree(directory, target)
        manifest = json.loads((target / "inputs_manifest.json").read_text())
        manifest["descriptor"]["implementation"]["version"] = version
        manifest["descriptor"]["implementation"]["logical_output_byte_limit"] = limit
        manifest["inputs_id"] = "forecast-inputs-sha256-" + canonical_sha256(manifest["descriptor"])
        (target / "inputs_manifest.json").write_text(json.dumps(manifest))
        assert (
            features_store.verify_inputs(target)["descriptor"]["tables"]
            == current["descriptor"]["tables"]
        )
        manifest["descriptor"]["implementation"]["logical_output_byte_limit"] = 5 * 1024**3
        manifest["inputs_id"] = "forecast-inputs-sha256-" + canonical_sha256(manifest["descriptor"])
        (target / "inputs_manifest.json").write_text(json.dumps(manifest))
        with pytest.raises(SnapshotError, match="unsupported_logical_budget"):
            features_store.verify_inputs(target)
    # The physical Parquet fits this budget, but the temporary index may not silently grow.
    physical = sum(
        ref["size_bytes"] for spec in current["tables"].values() for ref in spec["files"]
    )
    monkeypatch.setattr(features_store, "MAX_OUTPUT_BYTES", physical)
    # Tiny fixture SQLite page overhead is sufficient to exercise the separate index cap.
    with pytest.raises(SnapshotError, match="verification_index_byte_limit"):
        features_store.verify_inputs(directory)
