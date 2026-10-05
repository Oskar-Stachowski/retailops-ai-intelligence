"""Read exact existing GitHub artifacts; authentication never follows an external redirect."""

import hashlib
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from retailops_ai.source_snapshot.files import decode_json
from retailops_ai.stockout_campaign.archive import reserve
from retailops_ai.stockout_campaign.contract import SourceRef

REPOSITORY = "Oskar-Stachowski/retailops-ai-intelligence"
MAX_METADATA = 1024**2


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def _api(path: str) -> urllib.request.Request:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token or "\r" in token or "\n" in token:
        raise ValueError("stockout_final_github_credential_unavailable")
    return urllib.request.Request(
        "https://api.github.com/repos/" + REPOSITORY + "/" + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "retailops-ai-final-campaign",
        },
    )


def _external_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or not (host.endswith(".blob.core.windows.net") or host.endswith(".githubusercontent.com"))
    ):
        raise ValueError("stockout_final_untrusted_artifact_redirect")
    return value


def download(source: SourceRef, target: Path) -> None:
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
    ):
        raise ValueError("stockout_final_download_owned_runner_required")
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(_api("actions/artifacts/" + str(source.artifact_id)), timeout=30) as response:
        metadata = decode_json(response.read(MAX_METADATA + 1), limit=MAX_METADATA)
    if (
        metadata["id"] != source.artifact_id
        or metadata["expired"] is not False
        or metadata["size_in_bytes"] != source.artifact_bytes
        or metadata["workflow_run"]["id"] != source.workflow_run_id
    ):
        raise ValueError("stockout_final_github_artifact_identity")
    try:
        with opener.open(_api("actions/artifacts/" + str(source.artifact_id) + "/zip"), timeout=30):
            raise ValueError("stockout_final_artifact_redirect_required")
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise ValueError("stockout_final_github_artifact_unavailable") from None
        url = _external_url(exc.headers.get("Location", ""))
    if target.exists() or target.is_symlink():
        raise ValueError("stockout_final_download_target_exists")
    if shutil.disk_usage(target.parent).free - source.artifact_bytes < reserve():
        raise ValueError("stockout_final_download_free_disk_reserve")
    checksum, count = hashlib.sha256(), 0
    try:
        # Fresh unauthenticated request; no GitHub bearer header reaches storage.
        with (
            opener.open(urllib.request.Request(url), timeout=60) as response,  # noqa: S310 - validated HTTPS storage host, no authentication or redirects
            os.fdopen(
                os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb"
            ) as output,
        ):
            while block := response.read(1024**2):
                count += len(block)
                if (
                    count > source.artifact_bytes
                    or shutil.disk_usage(target.parent).free - len(block) < reserve()
                ):
                    raise ValueError("stockout_final_download_size_or_disk_limit")
                checksum.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        target.chmod(0o600)
        if count != source.artifact_bytes or checksum.hexdigest() != source.artifact_sha256:
            raise ValueError("stockout_final_download_digest_or_size_changed")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
