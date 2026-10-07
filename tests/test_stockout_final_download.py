"""Artifact identity, HTTPS redirect boundaries and incomplete-file cleanup."""

import hashlib
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from test_stockout_final_campaign import frozen as frozen
from test_stockout_final_campaign import recipes as recipes

from retailops_ai.stockout_campaign import download as subject


@pytest.mark.parametrize(
    "url",
    [
        "http://trusted.blob.core.windows.net/file",
        "file:///private/file",
        "https://github.com/artifact",
        "https://storage.blob.core.windows.net.attacker.test/file",
        "https://user:password@trusted.blob.core.windows.net/file",
        "https://trusted.blob.core.windows.net:8443/file",
    ],
)
def test_untrusted_redirect_rejected(url):
    with pytest.raises(ValueError, match="untrusted_artifact_redirect"):
        subject._external_url(url)


@pytest.mark.parametrize("fault", [None, "digest", "size", "expired", "wrong_run"])
def test_only_first_request_is_authenticated_and_bad_body_never_remains(
    frozen, tmp_path, monkeypatch, fault
):
    source = (
        frozen[0]
        .sources[0]
        .model_copy(
            update={"artifact_bytes": 1, "artifact_sha256": hashlib.sha256(b"x").hexdigest()}
        )
    )
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if len(calls) == 1:
                return io.BytesIO(
                    json.dumps(
                        dict(
                            id=source.artifact_id,
                            expired=fault == "expired",
                            size_in_bytes=1,
                            workflow_run=dict(
                                id=777 if fault == "wrong_run" else source.workflow_run_id
                            ),
                        )
                    ).encode()
                )
            if len(calls) == 2:
                raise HTTPError(
                    request.full_url,
                    302,
                    "redirect",
                    {
                        "Location": "https://trusted.blob.core.windows.net/archive?private_capability=fixture"
                    },
                    None,
                )
            return io.BytesIO(b"xx" if fault == "size" else b"y" if fault == "digest" else b"x")

    monkeypatch.setattr(subject.urllib.request, "build_opener", lambda *_: Opener())
    for key, value in dict(
        GITHUB_ACTIONS="true",
        RUNNER_OS="Linux",
        GITHUB_REPOSITORY=subject.REPOSITORY,
        GITHUB_TOKEN="fixture-token",  # noqa: S106 - synthetic fixture credential
    ).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(subject.shutil, "disk_usage", lambda _: SimpleNamespace(free=120 * 1024**3))
    target = tmp_path / "artifact.zip"
    if fault:
        with pytest.raises(ValueError):
            subject.download(source, target)
        assert not target.exists()
    else:
        subject.download(source, target)
        assert target.read_bytes() == b"x" and target.stat().st_mode & 0o777 == 0o600
    assert calls[0].get_header("Authorization") == "Bearer fixture-token"
    if len(calls) == 3:
        assert calls[2].get_header("Authorization") is None
