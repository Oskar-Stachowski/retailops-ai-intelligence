"""Resumable lifecycle operations; MLflow and AI database are not one transaction."""

import json
from contextlib import AbstractContextManager
from typing import Any, Protocol

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.model_lifecycle.contracts import (
    TEST_MODEL,
    Binding,
    Qualification,
    Release,
    Request,
    release_for,
)


class Registry(Protocol):
    def aliases(self, model: str) -> dict[str, str]: ...
    def source(self, run_id: str, digest: str, model: str) -> tuple[str, Qualification]: ...
    def validate(self, binding: Binding) -> None: ...
    def find(self, model: str, decision: str) -> list[str]: ...
    def create(self, model: str, run_id: str, source: str, decision: str, digest: str) -> str: ...
    def set_alias(self, model: str, alias: str, version: str) -> None: ...


class Journal(Protocol):
    def locked(self, model: str) -> AbstractContextManager[None]: ...
    def decision(self, decision_id: str) -> dict[str, Any] | None: ...
    def pending(self, model: str) -> list[str]: ...
    def prepare(self, record: dict[str, Any]) -> None: ...
    def step(self, decision_id: str, phase: str) -> dict[str, Any] | None: ...
    def append(self, decision_id: str, phase: str, record: dict[str, Any]) -> None: ...
    def binding(self, model: str, version: str) -> Binding: ...
    def bind(self, decision_id: str, binding: Binding) -> None: ...
    def rejected(self, model: str, version: str) -> bool: ...
    def release(self, release_id: str) -> Release: ...
    def active(self, model: str) -> Release | None: ...
    def activate(self, release: Release) -> None: ...


def require_promoter(actor: Principal) -> None:
    if "promoter" not in actor.roles or "model:decide" not in actor.capabilities:
        raise ValueError("promoter_authorization_required")


class Lifecycle:
    def __init__(self, registry: Registry, journal: Journal, *, environment: str) -> None:
        self.registry, self.journal, self.environment = registry, journal, environment

    def execute(self, request: Request, actor: Principal) -> dict[str, Any]:
        require_promoter(actor)
        # Validate again: callers cannot bypass validation through model_copy/construct.
        request = Request.model_validate_json(request.model_dump_json())
        if request.model_name == TEST_MODEL and self.environment != "test":
            raise ValueError("mechanics_registry_requires_test_environment")
        body = {"request": request.model_dump(mode="json"), "principal": actor.principal_id}
        digest = canonical_sha256(body)
        with self.journal.locked(request.model_name):
            record = self.journal.decision(request.decision_id)
            if record is not None:
                if record["request_sha256"] != digest or any(record[k] != body[k] for k in body):
                    raise ValueError("model_decision_id_conflict")
                completed = self.journal.step(request.decision_id, "completed")
                if completed is not None:
                    # Replaying an old decision must never revert a later release/alias.
                    return {**completed, "replayed": True}
            else:
                if self.journal.pending(request.model_name):
                    raise ValueError("model_incomplete_decision_requires_recovery")
                record = self.plan(request, body, digest)
                self.journal.prepare(record)
            if request.action == "register":
                return self.register(request, record)
            binding = self.journal.binding(request.model_name, str(request.model_version))
            self.check_binding(request, binding)
            if request.action != "reject":
                self.registry.validate(binding)
                if self.journal.rejected(request.model_name, binding.model_version):
                    raise ValueError("rejected_model_cannot_be_released")
            self.reconcile(request, record["before"], record["after"])
            release = record.get("release")
            if release is not None:
                # Only approved state changes here. A serving worker is integrated in 05.4/05.5.
                self.journal.activate(Release.model_validate_json(json.dumps(release)))
            result = {
                "decision_id": request.decision_id,
                "action": request.action,
                "model_name": request.model_name,
                "model_version": binding.model_version,
                "release_id": release["release_id"] if release else None,
                "runtime_status": "not_integrated",
                "replayed": False,
            }
            self.journal.append(request.decision_id, "completed", result)
            return result

    def check_binding(self, request: Request, binding: Binding) -> None:
        if (
            request.evidence_id != binding.qualification.evidence_id
            or request.qualification_sha256 != binding.qualification_sha256
        ):
            raise ValueError("model_decision_evidence_mismatch")

    def plan(self, request: Request, body: dict[str, Any], digest: str) -> dict[str, Any]:
        before = self.registry.aliases(request.model_name)
        active = self.journal.active(request.model_name)
        expected_champion = active.binding.model_version if active else None
        if before.get("champion") != expected_champion:
            raise ValueError("registry_champion_disagrees_with_approved_release")
        expected_rollback = active.previous_version if active else None
        if before.get("rollback") != expected_rollback:
            raise ValueError("registry_rollback_disagrees_with_approved_release")
        after = dict(before)
        release: Release | None = None
        source: dict[str, Any] | None = None
        if request.action == "register":
            uri, qualification = self.registry.source(
                str(request.mlflow_run_id), request.qualification_sha256, request.model_name
            )
            if request.evidence_id != qualification.evidence_id:
                raise ValueError("registration_evidence_mismatch")
            source = {"uri": uri, "qualification": qualification.model_dump(mode="json")}
        else:
            binding = self.journal.binding(request.model_name, str(request.model_version))
            self.check_binding(request, binding)
            if request.action == "reject":
                if active and binding.model_version in {
                    active.binding.model_version,
                    active.previous_version,
                }:
                    raise ValueError("cannot_reject_champion_or_rollback")
            else:
                if self.journal.rejected(request.model_name, binding.model_version):
                    raise ValueError("rejected_model_cannot_be_released")
                self.registry.validate(binding)
                if request.action == "promote":
                    if before.get("candidate") != binding.model_version:
                        raise ValueError("promotion_requires_candidate")
                    if expected_champion == binding.model_version:
                        raise ValueError("model_already_champion")
                    image = request.image_digest
                    restored = None
                else:
                    if active is None or active.previous_release_id is None:
                        raise ValueError("rollback_requires_previous_release")
                    previous = self.journal.release(active.previous_release_id)
                    if (
                        previous.binding != binding
                        or before.get("rollback") != binding.model_version
                    ):
                        raise ValueError("rollback_requires_exact_previous_release")
                    image, restored = previous.image_digest, previous.release_id
                release = release_for(
                    decision_id=request.decision_id,
                    binding=binding.model_dump(mode="json"),
                    image_digest=image,
                    previous_release_id=active.release_id if active else None,
                    previous_version=expected_champion,
                    restored_from_release_id=restored,
                )
                after["champion"] = binding.model_version
                if expected_champion is not None:
                    after["rollback"] = expected_champion
        return {
            **body,
            "request_sha256": digest,
            "before": before,
            "after": after,
            "release": release.model_dump(mode="json") if release else None,
            "source": source,
        }

    def register(self, request: Request, record: dict[str, Any]) -> dict[str, Any]:
        uri, qualification = self.registry.source(
            str(request.mlflow_run_id), request.qualification_sha256, request.model_name
        )
        if {"uri": uri, "qualification": qualification.model_dump(mode="json")} != record["source"]:
            raise ValueError("registration_source_changed")
        matches = self.registry.find(request.model_name, request.decision_id)
        attempted = self.journal.step(request.decision_id, "create_attempted")
        if len(matches) > 1 or (matches and attempted is None):
            raise ValueError("registry_version_creation_conflict")
        if matches:
            version = matches[0]
        elif attempted is not None:
            # No blind POST retry after a timeout/crash: MLflow has no creation idempotency key.
            raise ValueError("registry_version_creation_outcome_unknown")
        else:
            self.journal.append(request.decision_id, "create_attempted", {"source": uri})
            version = self.registry.create(
                request.model_name,
                str(request.mlflow_run_id),
                uri,
                request.decision_id,
                request.qualification_sha256,
            )
        binding = Binding(
            model_name=request.model_name,
            model_version=version,
            mlflow_run_id=str(request.mlflow_run_id),
            source_uri=uri,
            qualification_sha256=request.qualification_sha256,
            qualification=qualification,
        )
        self.registry.validate(binding)
        self.journal.bind(request.decision_id, binding)
        self.journal.append(request.decision_id, "version_bound", binding.model_dump(mode="json"))
        after = {**record["after"], "candidate": version}
        self.reconcile(request, record["before"], after)
        result = {
            "decision_id": request.decision_id,
            "action": "register",
            "model_name": request.model_name,
            "model_version": version,
            "release_id": None,
            "runtime_status": "not_integrated",
            "replayed": False,
        }
        self.journal.append(request.decision_id, "completed", result)
        return result

    def reconcile(self, request: Request, before: dict[str, str], after: dict[str, str]) -> None:
        # Rollback first: an interrupted promotion must not discard the known previous version.
        for alias in ("rollback", "champion", "candidate"):
            current = self.registry.aliases(request.model_name)
            if set(current) - {"candidate", "champion", "rollback"}:
                raise ValueError("uncontrolled_registry_alias")
            if any(
                current.get(k) not in {before.get(k), after.get(k)}
                for k in set(before) | set(after) | set(current)
            ):
                raise ValueError("registry_alias_changed_outside_decision")
            target = after.get(alias)
            if current.get(alias) != target:
                if target is None:
                    raise ValueError("unexpected_alias_requires_operator_review")
                self.registry.set_alias(request.model_name, alias, target)
        if self.registry.aliases(request.model_name) != after:
            raise ValueError("registry_alias_postcondition_failed")
        self.journal.append(request.decision_id, "aliases_verified", {"aliases": after})
