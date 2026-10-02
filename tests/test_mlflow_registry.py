"""Registry review must preserve the failed quality decision and promoter boundary."""

import hashlib
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_forecast_run import _archive

from retailops_ai.forecasting.run_contract import ArtifactReceipt, ForecastRunManifest
from retailops_ai.security.models import GrantTemplate
from retailops_ai.security.provision import provision

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mlflow_registry as registry  # noqa: E402


def _source(tmp_path):
    staged, original = _archive(tmp_path)
    for name, value in {
        "metrics.json": {
            "quality_status": "not_ready",
            "gate_counts": original.descriptor.gate_counts,
        },
        "handoff.json": {
            "original_run_id": original.run_id,
            "training_executed_in_this_run": False,
            "training_run_id": None,
            "registration_eligible": False,
            "promotion_eligible": False,
            "serving_eligible": False,
        },
    }.items():
        (staged / name).write_text(json.dumps(value))
    receipts = {
        name: ArtifactReceipt(
            size_bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        )
        for name in original.receipts
        for path in [staged / name]
    }
    manifest = ForecastRunManifest(
        run_id=original.run_id,
        descriptor=original.descriptor,
        started_at=original.started_at,
        completed_at=original.completed_at,
        receipts=receipts,
    )
    raw = manifest.model_dump_json().encode()
    reports = {
        "reports/" + name: (staged / name).read_bytes()
        for name in (
            "config.json",
            "metrics.json",
            "handoff.json",
            "model_card.json",
            "signature.json",
            "input_example.json",
        )
    }
    reports["reports/run_manifest.json"] = raw
    run = {
        "info": {"run_id": "a" * 32, "status": "FINISHED"},
        "data": {
            "tags": [
                {"key": k, "value": v}
                for k, v in {
                    "retailops.import_kind": "historical_evidence",
                    "retailops.import_status": "verified",
                    "retailops.run_kind": "forecast_evidence_export",
                    "retailops.registration_eligible": "false",
                    "retailops.promotion_eligible": "false",
                    "retailops.serving_eligible": "false",
                    "retailops.original_run_id": manifest.run_id,
                    "retailops.quality_status": "not_ready",
                    "retailops.export_started_at": manifest.started_at.isoformat(),
                    "retailops.export_completed_at": manifest.completed_at.isoformat(),
                }.items()
            ],
            "params": [
                {"key": k, "value": v}
                for k, v in {
                    "run_manifest_sha256": hashlib.sha256(raw).hexdigest(),
                    "config_sha256": hashlib.sha256(
                        (staged / "config.json").read_bytes()
                    ).hexdigest(),
                    "archive_size_bytes": "10",
                    "archive_sha256": "b" * 64,
                    "source_dataset_id": manifest.descriptor.source_dataset_id,
                    "curated_dataset_id": manifest.descriptor.curated_dataset_id,
                    "feature_set_id": manifest.descriptor.feature_set_id,
                    "label_dataset_id": manifest.descriptor.label_dataset_id,
                    "split_id": manifest.descriptor.split_id,
                    "backtest_id": manifest.descriptor.backtest_id,
                    "quality_id": manifest.descriptor.quality_id,
                    "source_code_commit": manifest.descriptor.source_code_commit,
                    "ai_code_commit": manifest.descriptor.ai_code_commit,
                    "dependency_lock_sha256": manifest.descriptor.dependency_lock_sha256,
                    "data_seed": str(manifest.descriptor.data_seed),
                    "model_seed": str(manifest.descriptor.model_seed),
                }.items()
            ],
        },
    }
    return run, reports


def test_historical_review_is_bound_to_artifacts_and_never_eligible(tmp_path, monkeypatch):
    source, reports = _source(tmp_path)
    monkeypatch.setattr(registry, "run", lambda _: source)
    monkeypatch.setattr(registry, "artifact", lambda _, name: reports[name])
    monkeypatch.setattr(registry.tracking, "artifact_path", lambda *_: "/archive")
    monkeypatch.setattr(registry.tracking, "remote_hash", lambda _: (10, "b" * 64))
    result = registry.review("a" * 32)
    assert result["quality_status"] == "not_ready"
    assert not result["eligible_for_registration"]
    assert result["model_version"] is None
    assert result["gate_counts"] == {"passed": 1, "failed": 1, "not_ready": 1}
    valid_metrics = reports["reports/metrics.json"]
    reports["reports/metrics.json"] = b'{"quality_status":"passed"}'
    with pytest.raises(ValueError, match="checksum"):
        registry.review("a" * 32)
    reports["reports/metrics.json"] = valid_metrics
    next(row for row in source["data"]["params"] if row["key"] == "source_dataset_id")["value"] = (
        "unrelated-source"
    )
    with pytest.raises(ValueError, match="manifest_binding"):
        registry.review("a" * 32)


def test_promoter_requires_role_capability_and_private_credential(tmp_path):
    template = GrantTemplate.model_validate_json(
        (
            Path(__file__).resolve().parents[1]
            / "contracts/access/v1/model-promoter.grant-template.example.json"
        ).read_bytes()
    )
    with pytest.raises(ValidationError, match="promoter_role"):
        GrantTemplate.model_validate(
            {
                **template.model_dump(),
                "grants": [{**template.grants[0].model_dump(), "roles": ["admin"]}],
            }
        )
    grant = tmp_path / "grant.json"
    grant.write_text(template.model_dump_json())
    output = tmp_path / "private"
    provision(grant, output, 1)
    policy = output / "api-access-policy.json"
    credential = output / "api-client-credentials.json"
    assert registry.principal(policy, credential) == "local-model-promoter"
    credential.chmod(0o644)
    with pytest.raises(ValueError, match="private_credentials"):
        registry.principal(policy, credential)


def test_incomplete_or_conflicting_decision_id_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "principal", lambda *_: "promoter")
    monkeypatch.setattr(
        registry, "review", lambda _: {"evidence_id": "run-1", "review_id": "review-1"}
    )
    monkeypatch.setattr(registry, "WORK", tmp_path / "work")
    monkeypatch.setattr(registry, "registry", lambda: {})
    monkeypatch.setattr(registry, "experiment", lambda: "1")
    monkeypatch.setattr(
        registry,
        "prior_decisions",
        lambda *_: [
            {
                "info": {"status": "RUNNING"},
                "data": {"tags": [{"key": "retailops.decision_sha256", "value": "a" * 64}]},
            }
        ],
    )
    with pytest.raises(ValueError, match="conflict_or_incomplete"):
        registry.reject(
            "a" * 32, "decision-registry-001", "Quality gate failed", Path("p"), Path("c")
        )


def test_complete_rejection_decision_replays_without_new_audit_run(tmp_path, monkeypatch):
    from retailops_ai.data_contracts.identity import canonical_sha256

    decision_id = "decision-registry-001"
    reason = "Quality gate failed"
    run_id = "a" * 32
    review = {"evidence_id": "run-1", "review_id": "review-1"}
    decision = {
        "schema_version": "1.0.0",
        "decision_id": decision_id,
        "action": "reject",
        "model_name": registry.MODEL,
        "model_version": None,
        "evidence_id": "run-1",
        "mlflow_run_id": run_id,
        "review_id": "review-1",
        "principal": "promoter",
        "reason": reason,
    }
    digest = canonical_sha256(decision)
    monkeypatch.setattr(registry, "principal", lambda *_: "promoter")
    monkeypatch.setattr(registry, "review", lambda _: review)
    monkeypatch.setattr(registry, "WORK", tmp_path / "work")
    monkeypatch.setattr(registry, "registry", lambda: {})
    monkeypatch.setattr(registry, "experiment", lambda: "1")
    monkeypatch.setattr(
        registry,
        "prior_decisions",
        lambda *_: [
            {
                "info": {"status": "FINISHED", "run_id": "b" * 32},
                "data": {
                    "tags": [
                        {"key": "retailops.decision_sha256", "value": digest},
                        {"key": "retailops.phase", "value": "verified"},
                    ]
                },
            }
        ],
    )
    monkeypatch.setattr(
        registry, "run", lambda _: {"info": {"run_id": run_id}, "data": {"tags": []}}
    )
    monkeypatch.setattr(
        registry,
        "artifact",
        lambda *_: json.dumps({**decision, "decision_sha256": digest, "review": review}).encode(),
    )
    observed = []
    monkeypatch.setattr(registry, "source_tag", lambda _, value: observed.append(value))
    result = registry.reject(run_id, decision_id, reason, Path("p"), Path("c"))
    assert result["status"] == "already_rejected"
    assert result["audit_run_id"] == "b" * 32
    assert observed == [decision_id]
