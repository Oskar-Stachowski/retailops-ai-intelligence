"""V12 lifecycle failures on explicit transport/source/review doubles, never real qualification."""

import copy
import hashlib
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_model_lifecycle import MemoryJournal
from test_v12_inference import IMAGE, actor, approve_fixture
from test_v12_inference import artifacts as artifacts
from test_v12_inference import inputs as inputs
from test_v12_inference import loaded as loaded
from test_v12_inference import prepared_input as prepared_input
from test_v12_inference import qualification as qualification
from test_v12_inference import tables as tables
from test_v12_inference import timeline as timeline

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.model_lifecycle import v12_mlflow
from retailops_ai.model_lifecycle.mlflow import MLflowRegistry
from retailops_ai.model_lifecycle.v12_evidence import load_evidence
from retailops_ai.model_lifecycle.v12_lifecycle import V12Lifecycle
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    TEST_MODEL,
    V12Binding,
    V12LifecycleRequest,
    V12ModelRelease,
    V12RegistrySource,
)
from retailops_ai.model_lifecycle.v12_registry import MLflowV12Registry, publish_approval
from retailops_ai.model_lifecycle.v12_release import load_approved_v12

ROOT = Path(__file__).resolve().parents[1]


class MemoryTransport(MLflowRegistry):
    """Only the HTTP/storage provider is doubled; v12 source/registry validation runs unchanged."""

    def __init__(self):
        super().__init__(environment="test")
        self.runs, self.experiments, self.versions, self.models, self.artifacts = {}, {}, {}, {}, {}
        self.calls = []
        self.fail_create_after = self.fail_create_before = False
        self.fail_alias = None

    def request(self, path, payload=None, *, limit=4 * 1024**2):
        self.calls.append((path, copy.deepcopy(payload)))
        if path.startswith("/api/2.0/mlflow-artifacts/artifacts/"):
            raw = self.artifacts[path]
            if len(raw) > limit:
                raise ValueError("mlflow_response_invalid")
            return raw
        parsed = urllib.parse.urlparse(path)
        query = urllib.parse.parse_qs(parsed.query)
        endpoint = parsed.path.removeprefix("/api/2.0/mlflow/")
        if endpoint == "experiments/get-by-name":
            if query["experiment_name"][0] not in self.experiments:
                raise ValueError("mlflow_resource_missing")
            result = {
                "experiment": {"experiment_id": self.experiments[query["experiment_name"][0]]}
            }
        elif endpoint == "experiments/create":
            identity = str(len(self.experiments) + 1)
            self.experiments[payload["name"]] = identity
            result = {"experiment_id": identity}
        elif endpoint == "runs/create":
            identity = f"{len(self.runs) + 1:032x}"
            run = dict(
                info=dict(
                    run_id=identity,
                    experiment_id=payload["experiment_id"],
                    status="RUNNING",
                    artifact_uri=f"mlflow-artifacts:/1/{identity}/artifacts",
                ),
                data=dict(tags=payload["tags"], params=[], metrics=[]),
            )
            self.runs[identity] = run
            result = {"run": run}
        elif endpoint == "runs/search":
            result = {
                "runs": [
                    r
                    for r in self.runs.values()
                    if r["info"]["experiment_id"] in payload["experiment_ids"]
                ]
            }
        elif endpoint == "runs/get":
            result = {"run": self.runs[query["run_id"][0]]}
        elif endpoint in ("runs/update", "runs/set-tag", "runs/log-batch"):
            run = self.runs[payload["run_id"]]
            if endpoint == "runs/update":
                run["info"]["status"] = payload["status"]
            elif endpoint == "runs/set-tag":
                tags = {t["key"]: t["value"] for t in run["data"]["tags"]}
                tags[payload["key"]] = payload["value"]
                run["data"]["tags"] = [dict(key=k, value=v) for k, v in tags.items()]
            else:
                run["data"]["params"], run["data"]["metrics"] = (
                    payload["params"],
                    payload["metrics"],
                )
            result = {}
        elif endpoint == "registered-models/get":
            name = query["name"][0]
            if name not in self.models:
                raise ValueError("mlflow_resource_missing")
            result = {
                "registered_model": {
                    "name": name,
                    "aliases": [dict(alias=k, version=v) for k, v in self.models[name].items()],
                }
            }
        elif endpoint == "registered-models/create":
            self.models[payload["name"]] = {}
            result = {}
        elif endpoint == "registered-models/alias":
            self.models[payload["name"]][payload["alias"]] = payload["version"]
            if self.fail_alias == payload["alias"]:
                self.fail_alias = None
                raise TimeoutError("lost_alias_response")
            result = {}
        elif endpoint == "model-versions/create":
            if self.fail_create_before:
                self.fail_create_before = False
                raise TimeoutError("no_create_response")
            version = str(len(self.versions) + 1)
            record = dict(
                name=payload["name"],
                version=version,
                source=payload["source"],
                run_id=payload["run_id"],
                status="READY",
                tags=payload["tags"],
            )
            self.versions[version] = record
            if self.fail_create_after:
                self.fail_create_after = False
                raise TimeoutError("lost_create_response")
            result = {"model_version": record}
        elif endpoint == "model-versions/get":
            result = {"model_version": self.versions[query["version"][0]]}
        elif endpoint == "model-versions/search":
            result = {"model_versions": list(self.versions.values())}
        else:
            raise AssertionError("unexpected provider operation " + endpoint)
        return canonical_bytes(result)


class MemoryRegistry(MLflowV12Registry):
    def __init__(self):
        super().__init__(environment="test")
        self.transport = MemoryTransport()
        self.fail_upload = False

    def upload(self, path, root, name, ref):
        raw = (root / name).read_bytes()
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == (ref.size_bytes, ref.sha256)
        self.transport.artifacts[path] = raw + b"changed" if self.fail_upload else raw


@pytest.fixture
def serving(loaded, qualification, tmp_path):
    package = approve_fixture(tmp_path, qualification, loaded)
    return package, load_approved_v12(
        package, loaded.root, loaded.python, release_id=package.name, image_digest=IMAGE
    )


@pytest.fixture
def backend(serving, tmp_path):
    registry = MemoryRegistry()
    package, loaded = serving
    evidence = load_evidence(loaded.export.root, loaded.export.python)
    campaign = v12_mlflow.import_evidence(evidence, registry, tmp_path / "campaign-import")
    imported = publish_approval(
        package,
        loaded,
        registry,
        campaign_mlflow_run_id=campaign["mlflow_run_id"],
        model=TEST_MODEL,
        actor=actor(),
        work=tmp_path / "approval-import",
    )
    return registry, imported, campaign


def request(imported, action, suffix, version=None, *, model=TEST_MODEL, **changes):
    raw = dict(
        decision_id="decision-v12-test-" + suffix,
        action=action,
        model_name=model,
        mlflow_run_id=imported["mlflow_run_id"] if action == "register" else None,
        model_version=version,
        approval_id=imported["approval_id"],
        approval_sha256=imported["approval_sha256"],
        reason="Explicit lifecycle unit test decision.",
        image_digest=IMAGE if action == "promote" else None,
    )
    raw.update(changes)
    return V12LifecycleRequest.model_validate_json(canonical_bytes(raw))


def test_approval_import_retains_original_evidence_and_is_idempotent(backend, serving, tmp_path):
    registry, imported, campaign = backend
    package, loaded = serving
    before = copy.deepcopy(registry.transport.runs[campaign["mlflow_run_id"]])
    repeat = publish_approval(
        package,
        loaded,
        registry,
        campaign_mlflow_run_id=campaign["mlflow_run_id"],
        model=TEST_MODEL,
        actor=actor(),
        work=tmp_path / "approval-import",
    )
    assert repeat["status"] == "already_imported"
    assert repeat["mlflow_run_id"] == imported["mlflow_run_id"]
    assert registry.transport.runs[campaign["mlflow_run_id"]] == before
    assert len(registry.transport.runs) == 2 and registry.transport.versions == {}
    assert (
        registry.source(imported["mlflow_run_id"], imported["approval_sha256"], TEST_MODEL).approval
        == loaded.release
    )


def test_two_versions_promote_rollback_and_old_replay_do_not_revert_new_head(backend):
    registry, imported, _ = backend
    journal = MemoryJournal()
    lifecycle = V12Lifecycle(registry, journal, environment="test")
    v1 = lifecycle.execute(request(imported, "register", "register1"), actor())["model_version"]
    first = lifecycle.execute(request(imported, "promote", "promote1", v1), actor())
    v2 = lifecycle.execute(request(imported, "register", "register2"), actor())["model_version"]
    second = lifecycle.execute(request(imported, "promote", "promote2", v2), actor())
    rollback = lifecycle.execute(request(imported, "rollback", "rollback1", v1), actor())
    active = journal.active(TEST_MODEL)
    assert active.release_id == rollback["release_id"]
    assert active.restored_from_release_id == first["release_id"]
    assert active.previous_release_id == second["release_id"]
    assert registry.aliases(TEST_MODEL) == dict(candidate=v2, champion=v1, rollback=v2)
    assert (
        lifecycle.execute(request(imported, "promote", "promote1", v1), actor())["replayed"] is True
    )
    assert journal.active(TEST_MODEL) == active


def test_lost_creation_response_recovers_exact_version_without_duplicate(backend):
    registry, imported, _ = backend
    journal = MemoryJournal()
    registry.transport.fail_create_after = True
    operation = request(imported, "register", "lostcreate")
    with pytest.raises(TimeoutError):
        V12Lifecycle(registry, journal, environment="test").execute(operation, actor())
    result = V12Lifecycle(registry, journal, environment="test").execute(operation, actor())
    assert result["model_version"] == "1" and len(registry.transport.versions) == 1


def test_unknown_creation_outcome_is_not_retried_and_blocks_other_decisions(backend):
    registry, imported, _ = backend
    journal = MemoryJournal()
    registry.transport.fail_create_before = True
    operation = request(imported, "register", "unknowncreate")
    lifecycle = V12Lifecycle(registry, journal, environment="test")
    with pytest.raises(TimeoutError):
        lifecycle.execute(operation, actor())
    with pytest.raises(ValueError, match="outcome_unknown"):
        lifecycle.execute(operation, actor())
    with pytest.raises(ValueError, match="pending_decision"):
        lifecycle.execute(request(imported, "register", "othercreate"), actor())
    assert len(registry.transport.versions) == 0


def test_interrupted_alias_updates_recover_without_switching_head_early(backend):
    registry, imported, _ = backend
    journal = MemoryJournal()
    lifecycle = V12Lifecycle(registry, journal, environment="test")
    version = lifecycle.execute(request(imported, "register", "first"), actor())["model_version"]
    registry.transport.fail_alias = "champion"
    operation = request(imported, "promote", "partialalias", version)
    with pytest.raises(TimeoutError):
        lifecycle.execute(operation, actor())
    assert journal.active(TEST_MODEL) is None
    resumed = V12Lifecycle(registry, journal, environment="test").execute(operation, actor())
    assert resumed["release_id"] == journal.active(TEST_MODEL).release_id


@pytest.mark.parametrize(
    "change", ["image", "approval", "digest", "external_alias", "failed_run", "wrong_source"]
)
def test_invalid_promotion_fails_before_head_or_decision_writes(backend, change):
    registry, imported, _ = backend
    journal = MemoryJournal()
    lifecycle = V12Lifecycle(registry, journal, environment="test")
    version = lifecycle.execute(request(imported, "register", "first"), actor())["model_version"]
    changes = {}
    if change == "image":
        changes["image_digest"] = "sha256:" + "0" * 64
    elif change == "approval":
        changes["approval_id"] = "v12-inference-release-sha256-" + "0" * 64
    elif change == "digest":
        changes["approval_sha256"] = "0" * 64
    elif change == "external_alias":
        registry.transport.models[TEST_MODEL]["champion"] = "999"
    elif change == "failed_run":
        registry.transport.runs[imported["mlflow_run_id"]]["info"]["status"] = "FAILED"
    else:
        registry.transport.versions[version]["source"] += "/substituted"
    with pytest.raises(ValueError):
        lifecycle.execute(request(imported, "promote", "bad", version, **changes), actor())
    assert journal.active(TEST_MODEL) is None and len(journal.decisions) == 1


def test_rejected_candidate_and_arbitrary_rollback_are_refused(backend):
    registry, imported, _ = backend
    journal = MemoryJournal()
    lifecycle = V12Lifecycle(registry, journal, environment="test")
    version = lifecycle.execute(request(imported, "register", "first"), actor())["model_version"]
    lifecycle.execute(request(imported, "reject", "reject1", version), actor())
    with pytest.raises(ValueError, match="rejected_version"):
        lifecycle.execute(request(imported, "promote", "badpromote", version), actor())
    with pytest.raises(ValueError):
        lifecycle.execute(request(imported, "rollback", "badrollback", version), actor())


def test_auth_namespace_and_conflicting_decision_ids(backend):
    registry, imported, _ = backend
    journal = MemoryJournal()
    lifecycle = V12Lifecycle(registry, journal, environment="test")
    with pytest.raises(ValueError):
        lifecycle.execute(
            request(imported, "register", "denied"),
            actor().__class__(
                "viewer", frozenset({"viewer"}), frozenset(), frozenset(), frozenset(), frozenset()
            ),
        )
    operation = request(imported, "register", "first")
    lifecycle.execute(operation, actor())
    with pytest.raises(ValueError, match="decision_conflict"):
        lifecycle.execute(
            operation.model_copy(update={"reason": "Different decision with reused identifier."}),
            actor(),
        )
    with pytest.raises(ValueError, match="test_environment"):
        V12Lifecycle(registry, journal, environment="local").execute(
            request(imported, "register", "badnamespace"), actor()
        )
    with pytest.raises(ValueError):
        MLflowV12Registry(environment="local").namespace(TEST_MODEL)


@pytest.mark.parametrize(
    "file",
    [
        "release.json",
        "qualification.json",
        "smoke.json",
        "reports/segments.json",
        "capsule_manifest.json",
    ],
)
def test_remote_capsule_tamper_prevents_registration(backend, file):
    registry, imported, _ = backend
    run = registry.transport.runs[imported["mlflow_run_id"]]
    path = v12_mlflow.artifact_path(run, "v12-release/" + file)
    registry.transport.artifacts[path] += b"changed"
    with pytest.raises(ValueError):
        V12Lifecycle(registry, MemoryJournal(), environment="test").execute(
            request(imported, "register", "badcapsule"), actor()
        )
    assert registry.transport.versions == {}


@pytest.mark.parametrize(
    "file",
    [
        "run_manifest.json",
        "signature.json",
        "model_card.json",
        "campaign/metrics.json",
        "replay/replay_receipt.json",
    ],
)
def test_original_campaign_proof_substitution_is_rejected(backend, file):
    registry, imported, campaign = backend
    path = v12_mlflow.artifact_path(registry.transport.runs[campaign["mlflow_run_id"]], file)
    registry.transport.artifacts[path] += b"changed"
    with pytest.raises(ValueError):
        registry.source(imported["mlflow_run_id"], imported["approval_sha256"], TEST_MODEL)


def test_new_expired_promotion_is_denied_but_prepared_decision_can_finish_metadata_recovery(
    backend, monkeypatch
):
    from retailops_ai.model_lifecycle import v12_registry

    registry, imported, _ = backend
    journal = MemoryJournal()
    lifecycle = V12Lifecycle(registry, journal, environment="test")
    version = lifecycle.execute(request(imported, "register", "first"), actor())["model_version"]
    registry.transport.fail_alias = "champion"
    operation = request(imported, "promote", "interrupted", version)
    with pytest.raises(TimeoutError):
        lifecycle.execute(operation, actor())

    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(days=2)

    monkeypatch.setattr(v12_registry, "datetime", Future)
    # Recovery may reconcile the existing plan; consumers still reject this expired approval.
    lifecycle.execute(operation, actor())
    with pytest.raises(ValueError, match="expired_or_future"):
        v12_registry.current_approval(journal.active(TEST_MODEL).binding.approval)
    with pytest.raises(ValueError, match="expired_or_future"):
        lifecycle.execute(request(imported, "register", "newexpired"), actor())


@pytest.mark.parametrize(
    "name,model",
    [
        ("source", V12RegistrySource),
        ("binding", V12Binding),
        ("request", V12LifecycleRequest),
        ("release", V12ModelRelease),
    ],
)
def test_v12_lifecycle_schema_snapshots(name, model):
    assert (
        ROOT / "contracts/model_lifecycle/v12" / (name + ".schema.json")
    ).read_bytes() == canonical_bytes(model.model_json_schema()) + b"\n"


def test_failed_upload_is_retained_and_not_automatically_retried(serving, tmp_path):
    registry = MemoryRegistry()
    package, loaded = serving
    campaign = v12_mlflow.import_evidence(
        load_evidence(loaded.export.root, loaded.export.python),
        registry,
        tmp_path / "campaign-import",
    )
    registry.fail_upload = True
    with pytest.raises(ValueError):
        publish_approval(
            package,
            loaded,
            registry,
            campaign_mlflow_run_id=campaign["mlflow_run_id"],
            model=TEST_MODEL,
            actor=actor(),
            work=tmp_path / "approval-import",
        )
    registry.fail_upload = False
    with pytest.raises(ValueError):
        publish_approval(
            package,
            loaded,
            registry,
            campaign_mlflow_run_id=campaign["mlflow_run_id"],
            model=TEST_MODEL,
            actor=actor(),
            work=tmp_path / "approval-import",
        )
    assert len(registry.transport.runs) == 2
    assert registry.transport.runs[f"{2:032x}"]["info"]["status"] == "FAILED"
    assert registry.transport.versions == {}
