"""Bounded transport fixtures use real stockout capsule verification, not fake quality approval."""

import copy

import pytest
from test_model_lifecycle import actor
from test_stockout_release import backend as backend
from test_stockout_release import capsule as capsule
from test_stockout_release import conditional as conditional
from test_stockout_release import context as context
from test_stockout_release import job as job
from test_stockout_release import records as records
from test_stockout_release import source as source

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.stockout_lifecycle.contract import MODEL, TEST_MODEL, StockoutBinding
from retailops_ai.stockout_lifecycle.publish import publish_approval
from retailops_ai.stockout_lifecycle.registry import MLflowStockoutRegistry
from retailops_ai.stockout_lifecycle.release import receipt


class Transport:
    def __init__(self):
        self.runs, self.artifacts, self.calls = {}, {}, []
        self.experiment = None
        self.versions, self.alias_state = {}, {}
        self.lose_create_response = False

    def api(self, path, payload=None):
        self.calls.append((path, copy.deepcopy(payload)))
        if path.startswith("experiments/get-by-name?"):
            if self.experiment is None:
                raise ValueError("mlflow_resource_missing")
            return dict(experiment=dict(experiment_id=self.experiment))
        if path == "experiments/create":
            self.experiment = "1"
            return dict(experiment_id="1")
        if path == "runs/search":
            return dict(runs=copy.deepcopy(list(self.runs.values())))
        if path == "runs/create":
            run_id = str(len(self.runs) + 1).zfill(32)
            run = dict(
                info=dict(
                    run_id=run_id,
                    status="RUNNING",
                    artifact_uri="mlflow-artifacts:/1/" + run_id + "/artifacts",
                ),
                data=dict(tags=copy.deepcopy(payload["tags"])),
            )
            self.runs[run_id] = run
            if self.lose_create_response:
                self.lose_create_response = False
                raise TimeoutError("fixture_lost_response_after_create")
            return dict(run=copy.deepcopy(run))
        if path.startswith("runs/get?"):
            return dict(run=copy.deepcopy(self.runs[path.rsplit("=", 1)[1]]))
        if path == "runs/set-tag":
            tags = self.runs[payload["run_id"]]["data"]["tags"]
            for tag in tags:
                if tag["key"] == payload["key"]:
                    tag["value"] = payload["value"]
                    break
            else:
                tags.append(dict(key=payload["key"], value=payload["value"]))
            return {}
        if path == "runs/update":
            self.runs[payload["run_id"]]["info"]["status"] = payload["status"]
            return {}
        if path.startswith("registered-models/get?"):
            return dict(
                registered_model=dict(
                    aliases=[dict(alias=k, version=v) for k, v in self.alias_state.items()]
                )
            )
        raise AssertionError("unhandled_fixture_endpoint:" + path)

    def request(self, path, *, limit):
        raw = self.artifacts[path]
        if len(raw) > limit:
            raise ValueError("mlflow_response_invalid")
        return raw

    def create(self, model, run, uri, decision, digest):
        version = str(len(self.versions) + 1)
        self.versions[version] = dict(
            name=model, run_id=run, source=uri, version=version, status="READY", decision=decision
        )
        return version

    def version(self, model, version):
        return copy.deepcopy(self.versions[version])

    def find(self, model, decision):
        return [v for v, row in self.versions.items() if row["decision"] == decision]

    def set_alias(self, model, alias, version):
        self.alias_state[alias] = version


class FixtureRegistry(MLflowStockoutRegistry):
    def upload(self, path, root, name, reference):
        raw = (root / name).read_bytes()
        assert receipt(raw) == reference
        self.transport.artifacts[path] = raw


@pytest.fixture
def registry():
    registry = FixtureRegistry(environment="test")
    registry.transport = Transport()
    return registry


def publish(capsule, registry, work):
    root, approval = capsule
    return publish_approval(
        root, registry, approval_id=approval.release_id, model=TEST_MODEL, actor=actor(), work=work
    )


def test_actual_capsule_import_roundtrip_and_registration_are_separate(capsule, registry, tmp_path):
    result = publish(capsule, registry, tmp_path / "work")
    assert result["status"] == "imported" and not result["registered"] and not result["promoted"]
    assert not registry.transport.versions and not registry.transport.alias_state
    source = registry.source(result["mlflow_run_id"], result["approval_sha256"], TEST_MODEL)
    assert source.approval == capsule[1]
    assert len(source.files) == 20 and len(registry.transport.artifacts) == 21
    replay = publish(capsule, registry, tmp_path / "work")
    assert replay["status"] == "already_imported" and len(registry.transport.runs) == 1
    version = registry.create(source, "decision-stockout-test-register")
    binding = StockoutBinding.model_validate_json(
        canonical_bytes({**source.model_dump(mode="json"), "model_version": version})
    )
    registry.validate(binding)
    assert registry.find(TEST_MODEL, "decision-stockout-test-register") == [version]
    registry.set_alias(TEST_MODEL, "candidate", version)
    assert registry.aliases(TEST_MODEL) == {"candidate": version}


@pytest.mark.parametrize(
    "change", ["status", "tags", "uri", "manifest", "model", "artifact", "version"]
)
def test_changed_remote_evidence_or_binding_is_rejected(capsule, registry, tmp_path, change):
    result = publish(capsule, registry, tmp_path / "work")
    source = registry.source(result["mlflow_run_id"], result["approval_sha256"], TEST_MODEL)
    version = registry.create(source, "decision-stockout-test-register")
    binding = StockoutBinding.model_validate_json(
        canonical_bytes({**source.model_dump(mode="json"), "model_version": version})
    )
    run = registry.transport.runs[result["mlflow_run_id"]]
    if change == "status":
        run["info"]["status"] = "RUNNING"
    elif change == "tags":
        run["data"]["tags"].append(copy.deepcopy(run["data"]["tags"][0]))
    elif change == "uri":
        run["info"]["artifact_uri"] = "https://untrusted.example/model"
    elif change == "manifest":
        key = next(k for k in registry.transport.artifacts if k.endswith("capsule_manifest.json"))
        registry.transport.artifacts[key] = b"{}"
    elif change == "artifact":
        key = next(k for k in registry.transport.artifacts if k.endswith("smoke.json"))
        registry.transport.artifacts[key] = b"{}"
    elif change == "model":
        binding = binding.model_copy(update={"model_name": MODEL})
    else:
        registry.transport.versions[version]["status"] = "PENDING_REGISTRATION"
    with pytest.raises(ValueError):
        registry.validate(binding)


def test_lost_creation_response_retains_single_unfinished_run_and_never_reposts(
    capsule, registry, tmp_path
):
    registry.transport.lose_create_response = True
    with pytest.raises(TimeoutError):
        publish(capsule, registry, tmp_path / "work")
    with pytest.raises(ValueError, match="finished_approval"):
        publish(capsule, registry, tmp_path / "work")
    assert len(registry.transport.runs) == 1
    assert sum(path == "runs/create" for path, _ in registry.transport.calls) == 1
    assert not registry.transport.alias_state


def test_publisher_checks_actor_and_purpose_before_remote_writes(capsule, registry, tmp_path):
    root, approval = capsule
    with pytest.raises(ValueError):
        publish_approval(
            root,
            registry,
            approval_id=approval.release_id,
            model=MODEL,
            actor=actor(),
            work=tmp_path / "work",
        )
    assert not registry.transport.calls
    from retailops_ai.domain.access import Principal

    with pytest.raises(ValueError):
        publish_approval(
            root,
            registry,
            approval_id=approval.release_id,
            model=TEST_MODEL,
            actor=Principal(
                "viewer", frozenset(), frozenset(), frozenset(), frozenset(), frozenset()
            ),
            work=tmp_path / "work",
        )
    assert not registry.transport.calls


@pytest.mark.parametrize(
    "model",
    ["retailops-demand-forecast-v12", "retailops-stockout-risk-test-mechanics", "unsafe'query"],
)
def test_local_registry_rejects_foreign_or_test_namespace(model):
    with pytest.raises(ValueError, match="namespace"):
        MLflowStockoutRegistry().aliases(model)


def test_registry_rejects_paths_ids_versions_and_uncontrolled_aliases_before_transport(registry):
    with pytest.raises(ValueError):
        registry.api("https://untrusted.example/")
    with pytest.raises(ValueError):
        registry.source("unsafe-query", "a" * 64, TEST_MODEL)
    with pytest.raises(ValueError):
        registry.set_alias(TEST_MODEL, "unexpected", "1")
    with pytest.raises(ValueError):
        registry.set_alias(TEST_MODEL, "champion", "0")
    assert not registry.transport.calls
