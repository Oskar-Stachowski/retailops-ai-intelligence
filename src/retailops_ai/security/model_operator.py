"""Authenticate local model operators from private policy and credential files."""

import json
import os
import stat
from pathlib import Path

from retailops_ai.domain.access import Principal
from retailops_ai.security.local import load_authority, strict_json


def model_operator(policy: Path, credentials: Path) -> Principal:
    fd = os.open(credentials, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_size > 131072
        ):
            raise ValueError("invalid_private_credentials")
        raw = stream.read(131073)
    strict_json(raw)
    document = json.loads(raw)
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "credentials"}
        or document["schema_version"] != "1.0"
        or not isinstance(document["credentials"], list)
        or len(document["credentials"]) != 1
    ):
        raise ValueError("single_credential_required")
    credential = document["credentials"][0]
    if (
        not isinstance(credential, dict)
        or set(credential) != {"principal_id", "bearer_token"}
        or not isinstance(credential["principal_id"], str)
        or not isinstance(credential["bearer_token"], str)
    ):
        raise ValueError("invalid_operator_credential_shape")
    actor = load_authority(policy).authenticate("Bearer " + credential["bearer_token"])
    if (
        actor is None
        or "promoter" not in actor.roles
        or "model:decide" not in actor.capabilities
        or actor.principal_id != credential["principal_id"]
    ):
        raise ValueError("promoter_authorization_required")
    return actor
