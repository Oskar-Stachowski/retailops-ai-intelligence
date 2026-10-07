"""Load a private policy snapshot and authenticate high-entropy opaque Bearer tokens."""

import hashlib
import json
import os
import re
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retailops_ai.domain.access import KnowledgeAccess, Principal, StockoutAccess
from retailops_ai.security.models import AccessPolicy

MAX_POLICY_BYTES = 131072
BEARER_PATTERN = r"[A-Za-z0-9_-]{43,128}"


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_field")
        result[key] = value
    return result


def invalid_constant(value: str) -> None:
    raise ValueError("nonfinite_json_number")


def strict_json(raw: bytes) -> None:
    json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=invalid_constant)


def load_private_policy(path: Path) -> AccessPolicy:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or info.st_size > MAX_POLICY_BYTES
            ):
                raise ValueError("invalid_private_file")
            raw = source.read(MAX_POLICY_BYTES + 1)
        if len(raw) > MAX_POLICY_BYTES:
            raise ValueError("policy_too_large")
        strict_json(raw)
        return AccessPolicy.model_validate_json(raw)
    except (OSError, ValueError, RecursionError):
        raise ValueError("access_policy_unavailable") from None


class LocalAccess:
    def __init__(self, policy: AccessPolicy | None) -> None:
        self._policy = policy
        self._principals: dict[str, Principal] = {}
        if policy is not None:
            for grant in policy.grants:
                scope = grant.scope
                knowledge = grant.knowledge_scope
                stockout = grant.stockout_scope
                self._principals[grant.principal_id] = Principal(
                    principal_id=grant.principal_id,
                    roles=frozenset(grant.roles),
                    capabilities=frozenset(grant.capabilities),
                    product_ids=frozenset(scope.product_ids) if scope else frozenset(),
                    selling_location_ids=frozenset(scope.selling_location_ids)
                    if scope
                    else frozenset(),
                    channels=frozenset(scope.channels) if scope else frozenset(),
                    knowledge=KnowledgeAccess(
                        environment=knowledge.environment,
                        repositories=frozenset(knowledge.repositories),
                        access_classes=frozenset(knowledge.access_classes),
                        document_statuses=frozenset(knowledge.document_statuses),
                    )
                    if knowledge
                    else None,
                    stockout=StockoutAccess(
                        product_ids=frozenset(stockout.product_ids),
                        stock_location_ids=frozenset(stockout.stock_location_ids),
                    )
                    if stockout
                    else None,
                )

    def authenticate(
        self, authorization: str | None, *, now: datetime | None = None
    ) -> Principal | None:
        if authorization is None or self._policy is None:
            return None
        scheme, separator, token = authorization.partition(" ")
        if (
            scheme.lower() != "bearer"
            or not separator
            or re.fullmatch(BEARER_PATTERN, token) is None
        ):
            return None
        fingerprint = token_fingerprint(token)
        current = now or datetime.now(UTC)
        matched = None
        # Scan every bounded entry, without an early return for a matching token.
        for credential in self._policy.credentials:
            equal = secrets.compare_digest(fingerprint, credential.token_sha256)
            if (
                equal
                and not credential.revoked
                and credential.not_before <= current < credential.expires_at
            ):
                matched = self._principals[credential.principal_id]
        return matched

    def rejects_metrics_reuse(self, metrics_token: str | None) -> bool:
        if self._policy is None or metrics_token is None:
            return False
        fingerprint = hashlib.sha256(metrics_token.encode("utf-8")).hexdigest()
        return any(
            secrets.compare_digest(fingerprint, c.token_sha256) for c in self._policy.credentials
        )

    def policy_metadata(self) -> tuple[str, int, int]:
        if self._policy is None:
            raise ValueError("access_policy_unavailable")
        return self._policy.policy_id, len(self._policy.grants), len(self._policy.credentials)


def load_authority(path: Path | None, metrics_token: str | None = None) -> LocalAccess:
    authority = LocalAccess(load_private_policy(path) if path else None)
    if authority.rejects_metrics_reuse(metrics_token):
        raise ValueError("access_policy_unavailable")
    return authority
