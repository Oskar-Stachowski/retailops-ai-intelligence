"""Resumable v12 registration/rejection/promotion/rollback, without a distributed transaction."""

from contextlib import AbstractContextManager
from typing import Any, Protocol

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.model_lifecycle.engine import require_promoter
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    TEST_MODEL,
    V12Binding,
    V12LifecycleRequest,
    V12ModelRelease,
    V12RegistrySource,
    database_release,
)


class Registry(Protocol):
    def aliases(self, model: str) -> dict[str, str]: ...
    def source(
        self, run_id: str, digest: str, model: str, *, current: bool = True
    ) -> V12RegistrySource: ...
    def validate(self, binding: V12Binding, *, current: bool = True) -> None: ...
    def find(self, model: str, decision: str) -> list[str]: ...
    def create(self, source: V12RegistrySource, decision: str) -> str: ...
    def set_alias(self, model: str, alias: str, version: str) -> None: ...


class Journal(Protocol):
    def locked(self, model: str) -> AbstractContextManager[None]: ...
    def decision(self, decision_id: str) -> dict[str, Any] | None: ...
    def pending(self, model: str) -> list[str]: ...
    def prepare(self, record: dict[str, Any]) -> None: ...
    def step(self, decision_id: str, phase: str) -> dict[str, Any] | None: ...
    def append(self, decision_id: str, phase: str, record: dict[str, Any]) -> None: ...
    def binding(self, model: str, version: str) -> V12Binding: ...
    def bind(self, decision_id: str, binding: V12Binding) -> None: ...
    def rejected(self, model: str, version: str) -> bool: ...
    def release(self, release_id: str) -> V12ModelRelease: ...
    def active(self, model: str) -> V12ModelRelease | None: ...
    def activate(self, release: V12ModelRelease) -> None: ...


class V12Lifecycle:
    def __init__(self, registry: Registry, journal: Journal, *, environment: str) -> None:
        self.registry, self.journal, self.environment = registry, journal, environment

    @staticmethod
    def check_binding(request: V12LifecycleRequest, binding: V12Binding) -> None:
        if (
            request.approval_id != binding.approval.release_id
            or request.approval_sha256 != binding.approval_sha256
        ):
            raise ValueError("v12_lifecycle_approval_pin_mismatch")

    def execute(self, request: V12LifecycleRequest, actor: Principal) -> dict[str, Any]:
        require_promoter(actor)
        request = V12LifecycleRequest.model_validate_json(request.model_dump_json())
        if request.model_name == TEST_MODEL and self.environment != "test":
            raise ValueError("v12_mechanics_requires_test_environment")
        body = dict(request=request.model_dump(mode="json"), principal=actor.principal_id)
        digest = canonical_sha256(body)
        with self.journal.locked(request.model_name):
            record = self.journal.decision(request.decision_id)
            recovering = record is not None
            if record is not None:
                if record["request_sha256"] != digest or any(
                    record[key] != body[key] for key in body
                ):
                    raise ValueError("v12_lifecycle_decision_conflict")
                completed = self.journal.step(request.decision_id, "completed")
                if completed is not None:
                    return {**completed, "replayed": True}
            else:
                if self.journal.pending(request.model_name):
                    raise ValueError("v12_lifecycle_pending_decision_requires_recovery")
                record = self.plan(request, body, digest)
                self.journal.prepare(record)
            if request.action == "register":
                return self.register(request, record, current=not recovering)
            binding = self.journal.binding(request.model_name, str(request.model_version))
            self.check_binding(request, binding)
            if request.action != "reject":
                self.registry.validate(binding, current=not recovering)
                if self.journal.rejected(request.model_name, binding.model_version):
                    raise ValueError("v12_lifecycle_rejected_version")
            self.reconcile(request, record["before"], record["after"])
            release = record["release"]
            if release is not None:
                self.journal.activate(V12ModelRelease.model_validate_json(canonical_bytes(release)))
            result = self.result(
                request, binding.model_version, release["release_id"] if release else None
            )
            # PostgreSQL activation and this completion append commit in one transaction.
            self.journal.append(request.decision_id, "completed", result)
            return result

    def plan(
        self, request: V12LifecycleRequest, body: dict[str, Any], digest: str
    ) -> dict[str, Any]:
        before = self.registry.aliases(request.model_name)
        active = self.journal.active(request.model_name)
        champion = active.binding.model_version if active else None
        rollback = active.previous_version if active else None
        if before.get("champion") != champion or before.get("rollback") != rollback:
            raise ValueError("v12_lifecycle_aliases_disagree_with_database_head")
        after, release, source = dict(before), None, None
        if request.action == "register":
            source = self.registry.source(
                str(request.mlflow_run_id), request.approval_sha256, request.model_name
            )
            if source.approval.release_id != request.approval_id:
                raise ValueError("v12_lifecycle_registration_approval_pin")
        else:
            binding = self.journal.binding(request.model_name, str(request.model_version))
            self.check_binding(request, binding)
            if request.action == "reject":
                if active and binding.model_version in {champion, rollback}:
                    raise ValueError("v12_lifecycle_cannot_reject_champion_or_rollback")
            else:
                if self.journal.rejected(request.model_name, binding.model_version):
                    raise ValueError("v12_lifecycle_rejected_version")
                self.registry.validate(binding)
                if request.action == "promote":
                    if (
                        before.get("candidate") != binding.model_version
                        or champion == binding.model_version
                    ):
                        raise ValueError("v12_lifecycle_promotion_requires_new_candidate")
                    if request.image_digest != binding.approval.approval.image_digest:
                        raise ValueError("v12_lifecycle_unreviewed_image")
                    restored = None
                else:
                    if active is None or active.previous_release_id is None:
                        raise ValueError("v12_lifecycle_rollback_requires_previous_release")
                    previous = self.journal.release(active.previous_release_id)
                    if (
                        previous.binding != binding
                        or before.get("rollback") != binding.model_version
                    ):
                        raise ValueError("v12_lifecycle_rollback_requires_exact_previous_release")
                    restored = previous.release_id
                release = database_release(
                    decision_id=request.decision_id,
                    binding=binding.model_dump(mode="json"),
                    image_digest=binding.approval.approval.image_digest,
                    previous_release_id=active.release_id if active else None,
                    previous_version=champion,
                    restored_from_release_id=restored,
                )
                after["champion"] = binding.model_version
                if champion is not None:
                    after["rollback"] = champion
        return {
            **body,
            "request_sha256": digest,
            "before": before,
            "after": after,
            "source": source.model_dump(mode="json") if source else None,
            "release": release.model_dump(mode="json") if release else None,
        }

    def register(
        self, request: V12LifecycleRequest, record: dict[str, Any], *, current: bool
    ) -> dict[str, Any]:
        source = self.registry.source(
            str(request.mlflow_run_id), request.approval_sha256, request.model_name, current=current
        )
        if source.model_dump(mode="json") != record["source"]:
            raise ValueError("v12_lifecycle_registration_source_changed")
        matches = self.registry.find(request.model_name, request.decision_id)
        attempted = self.journal.step(request.decision_id, "create_attempted")
        if len(matches) > 1 or (matches and attempted is None):
            raise ValueError("v12_lifecycle_version_creation_conflict")
        if matches:
            version = matches[0]
        elif attempted is not None:
            raise ValueError("v12_lifecycle_creation_outcome_unknown")
        else:
            self.journal.append(
                request.decision_id, "create_attempted", dict(source=source.source_uri)
            )
            version = self.registry.create(source, request.decision_id)
        binding = V12Binding.model_validate_json(
            canonical_bytes({**source.model_dump(mode="json"), "model_version": version})
        )
        self.registry.validate(binding, current=current)
        self.journal.bind(request.decision_id, binding)
        self.journal.append(request.decision_id, "version_bound", binding.model_dump(mode="json"))
        self.reconcile(request, record["before"], {**record["after"], "candidate": version})
        result = self.result(request, version, None)
        self.journal.append(request.decision_id, "completed", result)
        return result

    def reconcile(
        self, request: V12LifecycleRequest, before: dict[str, str], after: dict[str, str]
    ) -> None:
        for alias in ("rollback", "champion", "candidate"):
            current = self.registry.aliases(request.model_name)
            if set(current) - {"candidate", "champion", "rollback"} or any(
                current.get(key) not in {before.get(key), after.get(key)}
                for key in set(before) | set(after) | set(current)
            ):
                raise ValueError("v12_lifecycle_alias_changed_outside_decision")
            target = after.get(alias)
            if current.get(alias) != target:
                if target is None:
                    raise ValueError("v12_lifecycle_unexpected_alias")
                self.registry.set_alias(request.model_name, alias, target)
        if self.registry.aliases(request.model_name) != after:
            raise ValueError("v12_lifecycle_alias_postcondition")
        self.journal.append(request.decision_id, "aliases_verified", dict(aliases=after))

    @staticmethod
    def result(
        request: V12LifecycleRequest, version: str, release_id: str | None
    ) -> dict[str, Any]:
        return dict(
            decision_id=request.decision_id,
            action=request.action,
            model_name=request.model_name,
            model_version=version,
            release_id=release_id,
            runtime_status="not_integrated",
            replayed=False,
        )
