"""Shared recovery on stockout-only mechanics fixtures; no real quality approval."""

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from test_model_lifecycle import MemoryJournal, actor
from test_stockout_conditional_runtime import conditional as conditional
from test_stockout_runtime import context as context
from test_stockout_runtime import records as records

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.stockout_lifecycle.contract import (
    MODEL,
    REVIEW_GATES,
    TEST_MODEL,
    StockoutApproval,
    StockoutBinding,
    StockoutLifecycleRequest,
    StockoutQualification,
    StockoutRegistrySource,
    capsule_names,
)
from retailops_ai.stockout_lifecycle.engine import StockoutLifecycle


def sealed(model, field, prefix, raw):
    body = dict(raw)
    body[field] = prefix + canonical_sha256(body)
    return model.model_validate_json(canonical_bytes(body))


@pytest.fixture
def source(conditional):
    f, kw = conditional
    now = datetime.now(UTC)
    ref = dict(sha256="a" * 64, size_bytes=10)
    q = sealed(
        StockoutQualification,
        "qualification_id",
        "stockout-qualification-serving-sha256-",
        dict(
            version="stockout-serving-qualification-1.0.0",
            purpose="stockout_mechanics_only",
            recipe=kw["recipe"].model_dump(mode="json"),
            policy=kw["policy"].model_dump(mode="json"),
            model_card=ref,
            final_quality=None,
            final_campaign_id=None,
            quality_status="not_evaluated_mechanics_only",
            public_inputs=ref,
            smoke=ref,
            signature=ref,
            smoke_scope=dict(product_ids=[f.product_id], stock_location_ids=[f.stock_location_id]),
            smoke_as_of=f.as_of.isoformat().replace("+00:00", "Z"),
            smoke_rows=1,
            created_at=now.isoformat().replace("+00:00", "Z"),
            valid_until=(now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            source_packages_verified=True,
            complete_pipeline_verified=True,
            repeatability_verified=True,
            serving_eligible=False,
        ),
    )
    approval = sealed(
        StockoutApproval,
        "release_id",
        "stockout-approval-sha256-",
        dict(
            version="stockout-inference-approval-1.0.0",
            qualification=q.model_dump(mode="json"),
            approval=dict(
                qualification_id=q.qualification_id,
                image_digest="sha256:" + "b" * 64,
                gates={g: dict(status="passed", report=ref) for g in REVIEW_GATES},
                reason="Explicit mechanics fixture; no real production approval.",
            ),
            reviewed_by="fixture-reviewer",
            reviewed_at=now.isoformat().replace("+00:00", "Z"),
            serving_eligible=True,
            registered_in_mlflow=False,
            activated_as_champion=False,
        ),
    )
    approval_sha = canonical_sha256(approval.model_dump(mode="json"))
    return StockoutRegistrySource.model_validate_json(
        canonical_bytes(
            dict(
                model_name=TEST_MODEL,
                mlflow_run_id="a" * 32,
                source_uri="mlflow-artifacts:/1/" + "a" * 32 + "/artifacts/stockout-release",
                approval_sha256=approval_sha,
                approval=approval.model_dump(mode="json"),
                files={
                    n: dict(
                        sha256=approval_sha if n == "approval.json" else "a" * 64, size_bytes=10
                    )
                    for n in capsule_names(final=False)
                },
            )
        )
    )


class Registry:
    def __init__(self, source):
        self.original = source
        self.state, self.versions = {}, {}
        self.fail_create_before = self.fail_create_after = False
        self.fail_alias = None
        self.fail_validation = False

    def aliases(self, model):
        return dict(self.state)

    def source(self, run_id, digest, model, *, current=True):
        s = self.original
        if (run_id, digest, model) != (s.mlflow_run_id, s.approval_sha256, s.model_name):
            raise ValueError("fixture_source_pin")
        if (
            current
            and not s.approval.reviewed_at
            <= datetime.now(UTC)
            < s.approval.qualification.valid_until
        ):
            raise ValueError("fixture_approval_expired")
        return s

    def validate(self, binding, *, current=True):
        source = self.source(
            binding.mlflow_run_id, binding.approval_sha256, binding.model_name, current=current
        )
        if (
            self.fail_validation
            or binding != self.versions[binding.model_version]["binding"]
            or source.model_dump(mode="json")
            != binding.model_dump(mode="json", exclude={"model_version"})
        ):
            raise ValueError("fixture_version_changed")

    def find(self, model, decision):
        return [v for v, r in self.versions.items() if r["decision"] == decision]

    def create(self, source, decision):
        if self.fail_create_before:
            self.fail_create_before = False
            raise TimeoutError("fixture_no_create_response")
        version = str(len(self.versions) + 1)
        binding = StockoutBinding.model_validate_json(
            canonical_bytes({**source.model_dump(mode="json"), "model_version": version})
        )
        self.versions[version] = dict(binding=binding, decision=decision)
        if self.fail_create_after:
            self.fail_create_after = False
            raise TimeoutError("fixture_lost_create_response")
        return version

    def set_alias(self, model, alias, version):
        self.state[alias] = version
        if alias == self.fail_alias:
            self.fail_alias = None
            raise TimeoutError("fixture_lost_alias_response")


@pytest.fixture
def backend(source):
    registry, journal = Registry(source), MemoryJournal()
    return StockoutLifecycle(registry, journal, environment="test"), registry, journal


def request(registry, action, suffix, version=None, **changes):
    s = registry.original
    r = dict(
        decision_id="decision-stockout-test-" + suffix,
        action=action,
        model_name=s.model_name,
        mlflow_run_id=s.mlflow_run_id if action == "register" else None,
        model_version=version,
        approval_id=s.approval.release_id,
        approval_sha256=s.approval_sha256,
        image_digest=s.approval.approval.image_digest if action == "promote" else None,
        reason="Explicit stockout mechanics decision in isolated unit test.",
    )
    r.update(changes)
    return StockoutLifecycleRequest.model_validate_json(canonical_bytes(r))


def register(backend, suffix):
    lifecycle, registry, _ = backend
    return lifecycle.execute(request(registry, "register", suffix), actor())["model_version"]


def test_two_complete_versions_promote_rollback_and_replay_keep_the_current_head(backend):
    lifecycle, registry, journal = backend
    v1 = register(backend, "one")
    p1 = request(registry, "promote", "promote-one", v1)
    r1 = lifecycle.execute(p1, actor())
    first = journal.active(TEST_MODEL)
    pin = first.runtime_pin()
    q = first.binding.approval.qualification
    assert pin.recipe_content_sha256 == canonical_sha256(q.recipe.model_dump(mode="json"))
    assert pin.policy_content_sha256 == canonical_sha256(q.policy.model_dump(mode="json"))
    v2 = register(backend, "two")
    lifecycle.execute(request(registry, "promote", "promote-two", v2), actor())
    second = journal.active(TEST_MODEL)
    assert second.previous_release_id == first.release_id and registry.state["rollback"] == v1
    assert lifecycle.execute(p1, actor()) == {**r1, "replayed": True}
    assert journal.active(TEST_MODEL) == second
    lifecycle.execute(request(registry, "rollback", "rollback-one", v1), actor())
    restored = journal.active(TEST_MODEL)
    assert (
        restored.binding == first.binding and restored.restored_from_release_id == first.release_id
    )
    assert registry.state["champion"] == v1 and registry.state["rollback"] == v2


def test_lost_create_response_recovers_without_an_extra_version(backend):
    lifecycle, registry, journal = backend
    registry.fail_create_after = True
    r = request(registry, "register", "lost-create")
    with pytest.raises(TimeoutError):
        lifecycle.execute(r, actor())
    assert len(registry.versions) == 1 and not journal.bindings
    result = lifecycle.execute(r, actor())
    assert result["model_version"] == "1" and len(registry.versions) == 1


def test_uncertain_creation_is_not_retried_and_blocks_other_decisions(backend):
    lifecycle, registry, journal = backend
    registry.fail_create_before = True
    r = request(registry, "register", "uncertain-create")
    with pytest.raises(TimeoutError):
        lifecycle.execute(r, actor())
    with pytest.raises(ValueError, match="creation_outcome_unknown"):
        lifecycle.execute(r, actor())
    with pytest.raises(ValueError, match="pending_decision"):
        lifecycle.execute(request(registry, "register", "another-create"), actor())
    assert not registry.versions and not journal.bindings


@pytest.mark.parametrize("alias", ["candidate", "champion", "rollback"])
def test_alias_interruption_keeps_database_head_until_complete_then_recovers(backend, alias):
    lifecycle, registry, journal = backend
    v1 = register(backend, "before-alias")
    lifecycle.execute(request(registry, "promote", "before-promote", v1), actor())
    first = journal.active(TEST_MODEL)
    r = request(registry, "register", "alias-second")
    if alias != "candidate":
        v2 = lifecycle.execute(r, actor())["model_version"]
        r = request(registry, "promote", "alias-promote", v2)
    registry.fail_alias = alias
    with pytest.raises(TimeoutError):
        lifecycle.execute(r, actor())
    assert journal.active(TEST_MODEL) == first
    lifecycle.execute(r, actor())
    assert len(registry.versions) == 2
    if alias == "candidate":
        assert journal.active(TEST_MODEL) == first
    else:
        assert journal.active(TEST_MODEL).binding.model_version == "2"


@pytest.mark.parametrize("change", ["approval", "digest", "image", "external_alias", "source"])
def test_invalid_promotion_has_no_new_intent_or_head(backend, change):
    lifecycle, registry, journal = backend
    v = register(backend, "promote-invalid")
    changes = {}
    if change == "approval":
        changes["approval_id"] = "stockout-approval-sha256-" + "0" * 64
    elif change == "digest":
        changes["approval_sha256"] = "0" * 64
    elif change == "image":
        changes["image_digest"] = "sha256:" + "0" * 64
    elif change == "external_alias":
        registry.state["champion"] = "99"
    else:
        registry.fail_validation = True
    before = copy.deepcopy(journal.decisions)
    with pytest.raises(ValueError):
        lifecycle.execute(request(registry, "promote", "invalid", v, **changes), actor())
    assert journal.decisions == before and not journal.heads


def test_rejection_refuses_active_or_rollback_and_rejected_candidate(backend):
    lifecycle, registry, journal = backend
    v1 = register(backend, "reject-one")
    lifecycle.execute(request(registry, "promote", "reject-first-promote", v1), actor())
    with pytest.raises(ValueError, match="cannot_reject"):
        lifecycle.execute(request(registry, "reject", "reject-champion", v1), actor())
    v2 = register(backend, "reject-two")
    lifecycle.execute(request(registry, "reject", "reject-candidate", v2), actor())
    with pytest.raises(ValueError, match="rejected_version"):
        lifecycle.execute(request(registry, "promote", "reject-promote", v2), actor())
    with pytest.raises(ValueError, match="rejected_version"):
        lifecycle.execute(request(registry, "rollback", "unreviewed-rollback", v2), actor())
    assert journal.active(TEST_MODEL).binding.model_version == v1


def test_actor_and_conflicting_request_are_bound_to_the_audit(backend):
    lifecycle, registry, journal = backend
    r = request(registry, "register", "principal")
    with pytest.raises(ValueError, match="promoter_authorization_required"):
        lifecycle.execute(r, actor("reader"))
    assert not journal.decisions
    lifecycle.execute(r, actor())
    with pytest.raises(ValueError, match="decision_conflict"):
        lifecycle.execute(
            r.model_copy(update={"reason": "Changed reason using the same decision identity."}),
            actor(),
        )
    with pytest.raises(ValueError, match="decision_conflict"):
        lifecycle.execute(r, replace(actor(), principal_id="different-promoter"))


def test_mechanics_is_test_only_and_cannot_be_reclassified_as_production(source):
    registry, journal = Registry(source), MemoryJournal()
    lifecycle = StockoutLifecycle(registry, journal, environment="local")
    with pytest.raises(ValueError, match="mechanics_requires_test"):
        lifecycle.execute(request(registry, "register", "local"), actor())
    raw = source.model_dump(mode="json")
    raw["model_name"] = MODEL
    with pytest.raises(ValidationError, match="namespace"):
        StockoutRegistrySource.model_validate_json(canonical_bytes(raw))
    assert not registry.versions and not journal.decisions


@pytest.mark.parametrize("change", ["recipe", "final", "gate", "boolean", "identity", "scope"])
def test_approval_contract_rejects_resealed_false_or_incomplete_claims(source, change):
    raw = source.approval.model_dump(mode="json")
    if change == "recipe":
        raw["qualification"]["policy"]["pin"]["calibrator_sha256"] = "0" * 64
    elif change == "final":
        raw["qualification"]["quality_status"] = "passed_independent_final_campaign"
    elif change == "gate":
        raw["approval"]["gates"].pop("calibration")
    elif change == "boolean":
        raw["qualification"]["serving_eligible"] = 0
    elif change == "scope":
        raw["qualification"]["smoke_rows"] = 2
    else:
        raw["release_id"] = "stockout-approval-sha256-" + "0" * 64
    with pytest.raises(ValidationError):
        StockoutApproval.model_validate_json(canonical_bytes(raw))


@pytest.mark.parametrize(
    "changes",
    [dict(mlflow_run_id=None), dict(model_version="1"), dict(image_digest="sha256:" + "a" * 64)],
)
def test_register_refuses_ambiguous_inputs(source, changes):
    registry = Registry(source)
    with pytest.raises(ValidationError, match="request_boundary"):
        request(registry, "register", "bad-boundary", **changes)
