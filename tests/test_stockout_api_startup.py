"""Cold API assembly must not initialize stockout training or private preparation."""

import os
import subprocess
import sys
from pathlib import Path


def test_database_api_assembles_without_stockout_execution_imports():
    root = Path(__file__).resolve().parents[1]
    script = """
import sys
from importlib.abc import MetaPathFinder

class BlockStockoutExecution(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = (
            "retailops_ai.stockout_training", "retailops_ai.stockout_selection",
            "retailops_ai.stockout_lifecycle", "retailops_ai.stockout_preparation",
            "retailops_ai.stockout_temporal_storage", "retailops_ai.stockout_jobs.queue",
            "retailops_ai.stockout_jobs.reader", "retailops_ai.stockout_jobs.input_store",
            "retailops_ai.stockout_runtime.contracts", "retailops_ai.stockout_runtime.inputs",
        )
        if any(fullname == p or fullname.startswith(p + ".") for p in forbidden):
            raise AssertionError("eager stockout execution import: " + fullname)

sys.meta_path.insert(0, BlockStockoutExecution())
from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.stockout_jobs.lazy import LazyStockoutAdministration, LazyStockoutReader

app = create_app(Settings(
    APP_ENV="test", ARTIFACT_ROOT="./artifacts",
    DATABASE_URL="postgresql+psycopg://ai_app:fixture@127.0.0.1:65432/retailops_ai",
))
schema = app.openapi()
assert "/api/v1/stockout-runs" in schema["paths"]
assert "/api/v1/stockout-risks" in schema["paths"]
assert "StockoutJobRun" in schema["components"]["schemas"]
print("cold database API assembled without stockout execution imports")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "cold database API assembled" in result.stdout
