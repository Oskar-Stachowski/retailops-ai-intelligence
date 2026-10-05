"""Shared recoverable AI 05 protocol, with separately validated domain contracts.

Registry aliases are external side effects. The database journal records every
intent before those effects, and commits activation together with completion.
No domain may substitute its approval or release schema for another domain.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Generic, NoReturn, Protocol, TypeVar

from retailops_ai.data_contracts.common import Contract
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.model_lifecycle.engine import require_promoter

RequestT = TypeVar("RequestT", bound=Contract)
SourceT = TypeVar("SourceT", bound=Contract)
BindingT = TypeVar("BindingT", bound=Contract)
ReleaseT = TypeVar("ReleaseT", bound=Contract)
BindingContra = TypeVar("BindingContra", bound=Contract, contravariant=True)


class Registry(Protocol[SourceT, BindingContra]):
    def aliases(self, model: str) -> dict[str, str]: ...
    def source(self, run_id: str, digest: str, model: str, *, current: bool = True) -> SourceT: ...
    def validate(self, binding: BindingContra, *, current: bool = True) -> None: ...
    def find(self, model: str, decision: str) -> list[str]: ...
    def create(self, source: SourceT, decision: str) -> str: ...
    def set_alias(self, model: str, alias: str, version: str) -> None: ...


class Journal(Protocol[BindingT, ReleaseT]):
    def locked(self, model: str) -> AbstractContextManager[None]: ...
    def decision(self, decision_id: str) -> dict[str, Any] | None: ...
    def pending(self, model: str) -> list[str]: ...
    def prepare(self, record: dict[str, Any]) -> None: ...
    def step(self, decision_id: str, phase: str) -> dict[str, Any] | None: ...
    def append(self, decision_id: str, phase: str, record: dict[str, Any]) -> None: ...
    def binding(self, model: str, version: str) -> BindingT: ...
    def bind(self, decision_id: str, binding: BindingT) -> None: ...
    def rejected(self, model: str, version: str) -> bool: ...
    def release(self, release_id: str) -> ReleaseT: ...
    def active(self, model: str) -> ReleaseT | None: ...
    def activate(self, release: ReleaseT) -> None: ...


@dataclass(frozen=True)
class Contracts(Generic[RequestT, BindingT, ReleaseT]):
    request: type[RequestT]
    binding: type[BindingT]
    release: type[ReleaseT]
    database_release: Callable[..., ReleaseT]
    mechanics_model: str
    error_prefix: str
    runtime_status: str = "not_integrated"


class ReviewedLifecycle(Generic[RequestT, SourceT, BindingT, ReleaseT]):
    def __init__(
        self,
        registry: Registry[SourceT, BindingT],
        journal: Journal[BindingT, ReleaseT],
        contracts: Contracts[RequestT, BindingT, ReleaseT],
        *,
        environment: str,
    ) -> None:
        self.registry, self.journal = registry, journal
        self.contracts, self.environment = contracts, environment

    def fail(self, reason: str) -> NoReturn:
        raise ValueError(self.contracts.error_prefix + "_" + reason)

    def check_binding(self, request: RequestT, binding: BindingT) -> None:
        r, b = request.model_dump(mode="json"), binding.model_dump(mode="json")
        if (
            r["approval_id"] != b["approval"]["release_id"]
            or r["approval_sha256"] != b["approval_sha256"]
        ):
            self.fail("lifecycle_approval_pin_mismatch")

    def execute(self, request: RequestT, actor: Principal) -> dict[str, Any]:
        require_promoter(actor)
        request = self.contracts.request.model_validate_json(request.model_dump_json())
        r = request.model_dump(mode="json")
        model, decision = r["model_name"], r["decision_id"]
        if model == self.contracts.mechanics_model and self.environment != "test":
            self.fail("mechanics_requires_test_environment")
        body = dict(request=r, principal=actor.principal_id)
        digest = canonical_sha256(body)
        with self.journal.locked(model):
            record = self.journal.decision(decision)
            recovering = record is not None
            if record is not None:
                if record["request_sha256"] != digest or any(
                    record[key] != body[key] for key in body
                ):
                    self.fail("lifecycle_decision_conflict")
                completed = self.journal.step(decision, "completed")
                if completed is not None:
                    return {**completed, "replayed": True}
            else:
                if self.journal.pending(model):
                    self.fail("lifecycle_pending_decision_requires_recovery")
                record = self.plan(request, body, digest)
                self.journal.prepare(record)
            if r["action"] == "register":
                return self.register(request, record, current=not recovering)
            binding = self.journal.binding(model, str(r["model_version"]))
            self.check_binding(request, binding)
            if r["action"] != "reject":
                self.registry.validate(binding, current=not recovering)
                if self.journal.rejected(model, binding.model_dump(mode="json")["model_version"]):
                    self.fail("lifecycle_rejected_version")
            self.reconcile(request, record["before"], record["after"])
            release = record["release"]
            if release is not None:
                self.journal.activate(
                    self.contracts.release.model_validate_json(canonical_bytes(release))
                )
            result = self.result(
                request, str(r["model_version"]), release["release_id"] if release else None
            )
            self.journal.append(decision, "completed", result)
            return result

    def plan(self, request: RequestT, body: dict[str, Any], digest: str) -> dict[str, Any]:
        r = request.model_dump(mode="json")
        model = r["model_name"]
        before = self.registry.aliases(model)
        active = self.journal.active(model)
        a = active.model_dump(mode="json") if active else None
        champion = a["binding"]["model_version"] if a else None
        rollback = a["previous_version"] if a else None
        if before.get("champion") != champion or before.get("rollback") != rollback:
            self.fail("lifecycle_aliases_disagree_with_database_head")
        after, release, source = dict(before), None, None
        if r["action"] == "register":
            source = self.registry.source(str(r["mlflow_run_id"]), r["approval_sha256"], model)
            if source.model_dump(mode="json")["approval"]["release_id"] != r["approval_id"]:
                self.fail("lifecycle_registration_approval_pin")
        else:
            binding = self.journal.binding(model, str(r["model_version"]))
            self.check_binding(request, binding)
            b = binding.model_dump(mode="json")
            version = b["model_version"]
            if r["action"] == "reject":
                if a and version in {champion, rollback}:
                    self.fail("lifecycle_cannot_reject_champion_or_rollback")
            else:
                if self.journal.rejected(model, version):
                    self.fail("lifecycle_rejected_version")
                self.registry.validate(binding)
                if r["action"] == "promote":
                    if before.get("candidate") != version or champion == version:
                        self.fail("lifecycle_promotion_requires_new_candidate")
                    if r["image_digest"] != b["approval"]["approval"]["image_digest"]:
                        self.fail("lifecycle_unreviewed_image")
                    restored = None
                else:
                    if a is None or a["previous_release_id"] is None:
                        self.fail("lifecycle_rollback_requires_previous_release")
                    previous = self.journal.release(a["previous_release_id"]).model_dump(
                        mode="json"
                    )
                    if previous["binding"] != b or before.get("rollback") != version:
                        self.fail("lifecycle_rollback_requires_exact_previous_release")
                    restored = previous["release_id"]
                release = self.contracts.database_release(
                    decision_id=r["decision_id"],
                    binding=b,
                    image_digest=b["approval"]["approval"]["image_digest"],
                    previous_release_id=a["release_id"] if a else None,
                    previous_version=champion,
                    restored_from_release_id=restored,
                )
                after["champion"] = version
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
        self, request: RequestT, record: dict[str, Any], *, current: bool
    ) -> dict[str, Any]:
        r = request.model_dump(mode="json")
        model, decision = r["model_name"], r["decision_id"]
        source = self.registry.source(
            str(r["mlflow_run_id"]), r["approval_sha256"], model, current=current
        )
        s = source.model_dump(mode="json")
        if s != record["source"]:
            self.fail("lifecycle_registration_source_changed")
        matches = self.registry.find(model, decision)
        attempted = self.journal.step(decision, "create_attempted")
        if len(matches) > 1 or (matches and attempted is None):
            self.fail("lifecycle_version_creation_conflict")
        if matches:
            version = matches[0]
        elif attempted is not None:
            self.fail("lifecycle_creation_outcome_unknown")
        else:
            self.journal.append(decision, "create_attempted", dict(source=s["source_uri"]))
            version = self.registry.create(source, decision)
        binding = self.contracts.binding.model_validate_json(
            canonical_bytes({**s, "model_version": version})
        )
        self.registry.validate(binding, current=current)
        self.journal.bind(decision, binding)
        self.journal.append(decision, "version_bound", binding.model_dump(mode="json"))
        self.reconcile(request, record["before"], {**record["after"], "candidate": version})
        result = self.result(request, version, None)
        self.journal.append(decision, "completed", result)
        return result

    def reconcile(self, request: RequestT, before: dict[str, str], after: dict[str, str]) -> None:
        r = request.model_dump(mode="json")
        for alias in ("rollback", "champion", "candidate"):
            current = self.registry.aliases(r["model_name"])
            if set(current) - {"candidate", "champion", "rollback"} or any(
                current.get(key) not in {before.get(key), after.get(key)}
                for key in set(before) | set(after) | set(current)
            ):
                self.fail("lifecycle_alias_changed_outside_decision")
            target = after.get(alias)
            if current.get(alias) != target:
                if target is None:
                    self.fail("lifecycle_unexpected_alias")
                self.registry.set_alias(r["model_name"], alias, target)
        if self.registry.aliases(r["model_name"]) != after:
            self.fail("lifecycle_alias_postcondition")
        self.journal.append(r["decision_id"], "aliases_verified", dict(aliases=after))

    def result(self, request: RequestT, version: str, release_id: str | None) -> dict[str, Any]:
        r = request.model_dump(mode="json")
        return dict(
            decision_id=r["decision_id"],
            action=r["action"],
            model_name=r["model_name"],
            model_version=version,
            release_id=release_id,
            runtime_status=self.contracts.runtime_status,
            replayed=False,
        )
