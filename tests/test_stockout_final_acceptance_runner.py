"""Final integration cannot replace genuine qualification with mechanics or fabricated identity."""

import importlib.util
from pathlib import Path

import pytest
from test_stockout_lifecycle import conditional as conditional
from test_stockout_lifecycle import context as context
from test_stockout_lifecycle import records as records
from test_stockout_lifecycle import source as source

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.security.local import LocalAccess, load_private_policy


@pytest.fixture
def subject(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    path = root / "scripts/check_stockout_final_acceptance.py"
    spec = importlib.util.spec_from_file_location("stockout_final_acceptance_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mechanics_cannot_create_genuine_review_reports(subject, source, tmp_path):
    root = tmp_path / "qualified"
    root.mkdir(mode=0o700)
    (root / "qualification.json").write_bytes(
        canonical_bytes(source.approval.qualification.model_dump(mode="json"))
    )
    with pytest.raises(ValueError, match="real_quality_required"):
        subject.review(root, "sha256:" + "0" * 64, tmp_path / "review", {})
    assert not (tmp_path / "review").exists()


def test_disposable_roles_require_authentication_and_keep_physical_scope(subject, source, tmp_path):
    q = source.approval.qualification
    policy, tokens, promoter = subject.access(tmp_path, q)
    assert promoter.principal_id == "ai08-acceptance-promoter"
    assert promoter.roles == frozenset({"promoter"})
    assert promoter.capabilities == frozenset({"model:decide"})
    assert all(token not in policy.read_text() for token in tokens.values())
    backend = LocalAccess(load_private_policy(policy))
    assert backend.authenticate("Bearer forged") is None
    pipeline = backend.authenticate("Bearer " + tokens["ai08-acceptance-pipeline"])
    assert pipeline.stockout.product_ids == frozenset(q.smoke_scope.product_ids)
    assert pipeline.stockout.stock_location_ids == frozenset(q.smoke_scope.stock_location_ids)
    assert "model:decide" not in pipeline.capabilities
    outsider = backend.authenticate("Bearer " + tokens["ai08-acceptance-outsider"])
    assert not outsider.stockout.product_ids & pipeline.stockout.product_ids
