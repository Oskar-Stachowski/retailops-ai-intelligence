"""Transport must not invalidate the dependency identities of existing ML artifacts."""

from pathlib import Path

import pytest

from scripts.check_intelligence_delivery import check

CORE = Path("uv.lock")
DELIVERY = Path("tools/intelligence-delivery/uv.lock")
PROJECT = Path("tools/intelligence-delivery/pyproject.toml")


def test_delivery_preserves_frozen_core_and_numerical_versions():
    assert check(CORE, DELIVERY, PROJECT)["status"] == "passed"


def test_delivery_rejects_a_different_numerical_library(tmp_path):
    changed = tmp_path / "delivery.lock"
    changed.write_text(
        DELIVERY.read_text().replace(
            'name = "numpy"\nversion = "2.4.6"', 'name = "numpy"\nversion = "99.0.0"'
        )
    )
    with pytest.raises(ValueError, match="ml_dependency_drift"):
        check(CORE, changed, PROJECT)


def test_delivery_rejects_changed_core_identity(tmp_path):
    changed = tmp_path / "core.lock"
    changed.write_bytes(CORE.read_bytes() + b"\n# changed identity\n")
    with pytest.raises(ValueError, match="frozen_ml_lock_changed"):
        check(changed, DELIVERY, PROJECT)
