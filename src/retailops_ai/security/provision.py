"""Explicit offline provisioning into a new private directory; never print tokens."""

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from retailops_ai.security.local import (
    MAX_POLICY_BYTES,
    load_private_policy,
    strict_json,
    token_fingerprint,
)
from retailops_ai.security.models import AccessPolicy, GrantTemplate


def provision(template_path: Path, output_dir: Path, ttl_hours: int) -> None:
    if not 1 <= ttl_hours <= 24:
        raise ValueError("invalid_credential_lifetime")
    with template_path.open("rb") as source:
        raw = source.read(MAX_POLICY_BYTES + 1)
    if len(raw) > MAX_POLICY_BYTES:
        raise ValueError("template_too_large")
    strict_json(raw)
    template = GrantTemplate.model_validate_json(raw)
    now = datetime.now(UTC)
    credentials, clients = [], []
    for grant in template.grants:
        token = secrets.token_urlsafe(32)
        credentials.append(
            {
                "token_sha256": token_fingerprint(token),
                "principal_id": grant.principal_id,
                "not_before": now.isoformat(),
                "expires_at": (now + timedelta(hours=ttl_hours)).isoformat(),
                "revoked": False,
            }
        )
        clients.append({"principal_id": grant.principal_id, "bearer_token": token})
    document = {**template.model_dump(mode="json"), "credentials": credentials}
    policy = AccessPolicy.model_validate_json(json.dumps(document))
    encoded = policy.model_dump_json(indent=2).encode()
    if len(encoded) > MAX_POLICY_BYTES:
        raise ValueError("policy_too_large")
    # Refuse an existing directory, including symlinks; never replace earlier credentials.
    output_dir.mkdir(mode=0o700)
    for name, content in (
        ("api-access-policy.json", encoded),
        (
            "api-client-credentials.json",
            json.dumps({"schema_version": "1.0", "credentials": clients}, indent=2).encode(),
        ),
    ):
        fd = os.open(output_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(content + b"\n")
    load_private_policy(output_dir / "api-access-policy.json")
