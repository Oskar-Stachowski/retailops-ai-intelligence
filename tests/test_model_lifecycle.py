"""Failures must not silently create versions, switch runtime or overwrite history."""

import copy
import hashlib
import json
from contextlib import contextmanager

import pytest
from pydantic import ValidationError
from test_forecast_features import tables as tables

from retailops_ai.domain.access import Principal
from retailops_ai.model_lifecycle.contracts import MODEL, TEST_MODEL, Request
from retailops_ai.model_lifecycle.engine import Lifecycle
from retailops_ai.model_lifecycle.mechanics import capsule
from retailops_ai.model_lifecycle.mlflow import MLflowRegistry


def actor(role="promoter"):
    return Principal(
        "local-" + role,
        frozenset([role]),
        frozenset(["model:decide"]),
        frozenset(),
        frozenset(),
        frozenset(),
    )


class MemoryJournal:
    def __init__(self):
        self.decisions, self.steps, self.bindings, self.releases, self.heads = {}, {}, {}, {}, {}

    @contextmanager
    def locked(self, model):
        yield

    def decision(self, decision):
        return self.decisions.get(decision)

    def pending(self, model):
        return [
            k
            for k, v in self.decisions.items()
            if v["request"]["model_name"] == model and (k, "completed") not in self.steps
        ]

    def prepare(self, record):
        self.decisions[record["request"]["decision_id"]] = copy.deepcopy(record)

    def step(self, decision, phase):
        return self.steps.get((decision, phase))

    def append(self, decision, phase, record):
        assert (decision, phase) not in self.steps or self.steps[decision, phase] == record
        self.steps[decision, phase] = copy.deepcopy(record)

    def binding(self, model, version):
        return self.bindings[(model, version)]

    def bind(self, decision, binding):
        self.bindings[(binding.model_name, binding.model_version)] = binding

    def rejected(self, model, version):
        return any(
            v["request"]["action"] == "reject"
            and v["request"]["model_name"] == model
            and v["request"]["model_version"] == version
            and (k, "completed") in self.steps
            for k, v in self.decisions.items()
        )

    def release(self, release):
        return self.releases[release]

    def active(self, model):
        return self.heads.get(model)

    def activate(self, release):
        self.releases[release.release_id] = release
        self.heads[release.binding.model_name] = release


class FakeRegistry:
    def __init__(self):
        self.state, self.versions, self.sources = {}, {}, {}
        self.fail_alias, self.fail_create_after_commit = False, False

    def aliases(self, model):
        return dict(self.state)

    def source(self, run, digest, model):
        qualification, files = self.sources[run]
        assert hashlib.sha256(files["qualification.json"]).hexdigest() == digest
        return "mlflow-artifacts:/1/" + run + "/artifacts/lifecycle", qualification

    def validate(self, binding):
        if self.versions[binding.model_version]["run"] != binding.mlflow_run_id:
            raise ValueError("immutable_registry_binding_changed")

    def find(self, model, decision):
        return [k for k, v in self.versions.items() if v["decision"] == decision]

    def create(self, model, run, source, decision, digest):
        version = str(len(self.versions) + 1)
        self.versions[version] = {"run": run, "decision": decision}
        if self.fail_create_after_commit:
            self.fail_create_after_commit = False
            raise TimeoutError("lost_response")
        return version

    def set_alias(self, model, alias, version):
        self.state[alias] = version
        if self.fail_alias:
            self.fail_alias = False
            raise TimeoutError("lost_alias_response")


def setup():
    backend, journal = FakeRegistry(), MemoryJournal()
    return Lifecycle(backend, journal, environment="test"), backend, journal


def request(backend, action, n, version=None, **extra):
    run = str(n) * 32
    if action == "register":
        backend.sources[run] = capsule(float(n), "mechanics-evidence-" + str(n))
    else:
        run = backend.versions[version]["run"]
    qualification, files = backend.sources[run]
    return Request.model_validate_json(
        json.dumps(
            {
                "action": action,
                "model_name": TEST_MODEL,
                "decision_id": "decision-mechanics-" + action + "-" + str(n),
                "mlflow_run_id": run if action == "register" else None,
                "model_version": version,
                "evidence_id": qualification.evidence_id,
                "qualification_sha256": hashlib.sha256(files["qualification.json"]).hexdigest(),
                "reason": "Explicit mechanics decision",
                "image_digest": "sha256:" + str(n) * 64 if action == "promote" else None,
                **extra,
            }
        )
    )


def test_two_versions_promote_rollback_and_old_replay_preserve_pinned_release():
    lifecycle, backend, journal = setup()
    r1 = request(backend, "register", 1)
    lifecycle.execute(r1, actor())
    p1 = request(backend, "promote", 1, "1")
    first = lifecycle.execute(p1, actor())
    release1 = journal.active(TEST_MODEL)
    assert release1.previous_version is None and release1.previous_release_id is None
    lifecycle.execute(request(backend, "register", 2), actor())
    lifecycle.execute(request(backend, "promote", 2, "2"), actor())
    assert backend.state == {"candidate": "2", "champion": "2", "rollback": "1"}
    assert journal.release(first["release_id"]) == release1
    assert lifecycle.execute(p1, actor())["replayed"]
    assert backend.state["champion"] == "2"
    lifecycle.execute(request(backend, "rollback", 3, "1"), actor())
    restored = journal.active(TEST_MODEL)
    assert restored.binding == release1.binding and restored.image_digest == release1.image_digest
    assert restored.restored_from_release_id == release1.release_id
    assert backend.state == {"candidate": "2", "champion": "1", "rollback": "2"}


def test_registration_recovers_lost_create_response_without_second_version():
    lifecycle, backend, journal = setup()
    req = request(backend, "register", 1)
    backend.fail_create_after_commit = True
    with pytest.raises(TimeoutError):
        lifecycle.execute(req, actor())
    assert not backend.state and len(backend.versions) == 1
    assert lifecycle.execute(req, actor())["model_version"] == "1"
    assert len(backend.versions) == 1 and len(journal.bindings) == 1


def test_unknown_registration_outcome_and_other_decisions_fail_closed():
    lifecycle, backend, journal = setup()
    req = request(backend, "register", 1)
    backend.fail_create_after_commit = True
    with pytest.raises(TimeoutError):
        lifecycle.execute(req, actor())
    backend.versions.clear()  # no observable proof of a completed POST
    with pytest.raises(ValueError, match="outcome_unknown"):
        lifecycle.execute(req, actor())
    with pytest.raises(ValueError, match="requires_recovery"):
        lifecycle.execute(request(backend, "register", 2), actor())


def test_partial_alias_update_recovers_from_new_process_and_external_drift_blocks():
    lifecycle, backend, journal = setup()
    lifecycle.execute(request(backend, "register", 1), actor())
    lifecycle.execute(request(backend, "promote", 1, "1"), actor())
    lifecycle.execute(request(backend, "register", 2), actor())
    req = request(backend, "promote", 2, "2")
    backend.fail_alias = True
    with pytest.raises(TimeoutError):
        lifecycle.execute(req, actor())
    assert journal.active(TEST_MODEL).binding.model_version == "1"
    assert backend.state["rollback"] == "1"
    backend.state["champion"] = "99"
    with pytest.raises(ValueError, match="outside_decision"):
        Lifecycle(backend, journal, environment="test").execute(req, actor())
    backend.state["champion"] = "1"
    Lifecycle(backend, journal, environment="test").execute(req, actor())
    assert journal.active(TEST_MODEL).binding.model_version == "2"


def test_auth_conflicting_id_rejection_and_rollback_without_history():
    lifecycle, backend, journal = setup()
    req = request(backend, "register", 1)
    with pytest.raises(ValueError, match="authorization"):
        lifecycle.execute(req, actor("viewer"))
    assert not backend.versions and not journal.decisions
    with pytest.raises(ValueError, match="test_environment"):
        Lifecycle(backend, journal, environment="local").execute(req, actor())
    lifecycle.execute(req, actor())
    with pytest.raises(ValueError, match="id_conflict"):
        lifecycle.execute(req.model_copy(update={"reason": "Changed decision reason"}), actor())
    with pytest.raises(ValueError, match="previous_release"):
        lifecycle.execute(request(backend, "rollback", 2, "1"), actor())
    lifecycle.execute(request(backend, "reject", 3, "1"), actor())
    assert backend.state == {"candidate": "1"}
    with pytest.raises(ValueError, match="rejected_model"):
        lifecycle.execute(request(backend, "promote", 4, "1"), actor())


class CapsuleClient(MLflowRegistry):
    def __init__(self, files, digest, **kwargs):
        super().__init__(**kwargs)
        self.files, self.digest = files, digest

    def api(self, path, payload=None):
        return {
            "run": {
                "info": {"status": "FINISHED", "artifact_uri": "mlflow-artifacts:/1/run/artifacts"},
                "data": {"tags": [{"key": "retailops.qualification_sha256", "value": self.digest}]},
            }
        }

    def artifact(self, uri, name, *, limit=0):
        return self.files[name]


def test_capsule_load_smoke_checksum_and_fixture_namespace():
    qualification, files = capsule(2.0, "fixture-evidence")
    digest = hashlib.sha256(files["qualification.json"]).hexdigest()
    client = CapsuleClient(files, digest, environment="test")
    assert client.source("a" * 32, digest, TEST_MODEL)[1] == qualification
    with pytest.raises(ValueError, match="namespace"):
        client.source("a" * 32, digest, MODEL)
    files["model.json"] = b'{"format":"mechanics-v1","bias":100}'
    with pytest.raises(ValueError, match="checksum"):
        client.source("a" * 32, digest, TEST_MODEL)


def test_missing_gates_alias_versions_and_arbitrary_pickle_sources_are_rejected():
    with pytest.raises(ValidationError):
        Request(
            action="promote",
            model_version="champion",
            evidence_id="evidence",
            qualification_sha256="a" * 64,
            decision_id="decision-invalid-version",
            reason="Explicit decision reason",
            image_digest="sha256:" + "b" * 64,
        )
    client = MLflowRegistry()
    for uri in (
        "file:/tmp/model.pkl",
        "https://example.com/model",
        "mlflow-artifacts:/1/../model",
        "mlflow-artifacts:/1/%2e%2e/model",
    ):
        with pytest.raises(ValueError, match="untrusted"):
            client.artifact(uri, "model.json")


@pytest.mark.parametrize("change", ["failed_gate", "expired", "bad_output"])
def test_bound_but_failed_or_expired_capsule_cannot_pass_review(change):
    from retailops_ai.model_lifecycle.contracts import Qualification

    qualification, files = capsule(1.0, "fixture-evidence")
    document = qualification.model_dump(mode="json")
    if change == "bad_output":
        document["expected_output_sha256"] = "0" * 64
    else:
        name = (
            "gate_segments.json"
            if change == "failed_gate"
            else "gate_freshness_drift_compatibility.json"
        )
        report = json.loads(files[name])
        if change == "failed_gate":
            report["status"] = "failed"
            document["gates"]["segments"]["status"] = "failed"
        else:
            report["valid_until"] = "2020-01-01T00:00:00+00:00"
        files[name] = json.dumps(report).encode()
        gate = "segments" if change == "failed_gate" else "freshness_drift_compatibility"
        document["gates"][gate]["report"] = {
            "sha256": hashlib.sha256(files[name]).hexdigest(),
            "size_bytes": len(files[name]),
        }
    files["qualification.json"] = (
        Qualification.model_validate_json(json.dumps(document)).model_dump_json().encode()
    )
    digest = hashlib.sha256(files["qualification.json"]).hexdigest()
    client = CapsuleClient(files, digest, environment="test")
    with pytest.raises(ValueError, match="gate_not_passed|expired|load_smoke_failed"):
        client.source("a" * 32, digest, TEST_MODEL)


def test_historical_qualification_contract_does_not_require_fictitious_training():
    from retailops_ai.model_lifecycle.contracts import Qualification

    qualification, _ = capsule(1.0, "historical-evidence")
    value = {
        **qualification.model_dump(mode="json"),
        "purpose": "qualified_forecast",
        "original_run_kind": "historical_evidence",
        "flavor": "baseline-json-v1",
        "model_family": "baseline",
    }
    assert (
        Qualification.model_validate_json(json.dumps(value)).original_run_kind
        == "historical_evidence"
    )


def test_serialized_baseline_reuses_predictor_and_refuses_pre_selection_origin(tables):
    from datetime import timedelta

    from test_forecast_features import rows

    from retailops_ai.data_contracts.identity import canonical_sha256
    from retailops_ai.forecasting.evaluation_contract import BaselinePolicy
    from retailops_ai.model_lifecycle.baseline import (
        BaselineInput,
        BaselinePipeline,
        predict_examples,
    )

    history, inputs = rows(tables)
    recipe = {
        "format": "forecast-baseline-v1",
        "model": "last_observed",
        "feature_set_id": "features-sha256-" + "a" * 64,
        "selection_cutoff": (history.forecast_origin - timedelta(days=1))
        .isoformat()
        .replace("+00:00", "Z"),
        "policy": BaselinePolicy().model_dump(mode="json"),
    }
    pipeline = BaselinePipeline.model_validate_json(
        json.dumps({"model_id": "model-sha256-" + canonical_sha256(recipe), **recipe})
    )
    examples = [BaselineInput(row=row, history=history) for row in inputs[:2]]
    assert predict_examples(pipeline, examples) == [39.0, 39.0]
    later = {
        **recipe,
        "selection_cutoff": history.forecast_origin.isoformat().replace("+00:00", "Z"),
    }
    pipeline = BaselinePipeline.model_validate_json(
        json.dumps({"model_id": "model-sha256-" + canonical_sha256(later), **later})
    )
    with pytest.raises(ValueError, match="not_known_at_origin"):
        predict_examples(pipeline, examples)


def test_model_migration_has_no_accidental_parameters_and_refuses_history_downgrade(monkeypatch):
    from importlib import import_module

    from sqlalchemy import text

    module = import_module("retailops_ai.migrations.versions.0009_model_lifecycle")
    commands = []
    monkeypatch.setattr(module.op, "execute", commands.append)
    module.upgrade()
    assert commands and not text(commands[0])._bindparams
    with pytest.raises(RuntimeError, match="backup_restore"):
        module.downgrade()
