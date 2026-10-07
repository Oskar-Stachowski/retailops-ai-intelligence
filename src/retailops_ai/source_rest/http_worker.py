"""One bounded GET in a disposable process. Never print credentials or raw errors."""

from __future__ import annotations

import base64
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MAX_BODY = 1048576


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("request_too_large")
        request = json.loads(raw)
        if urlsplit(request["url"]).scheme not in ("https", "http"):
            raise ValueError("invalid_scheme")
        context = ssl.create_default_context()
        for path in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            if Path(path).is_file():
                context = ssl.create_default_context(cafile=path)
                break
        opener = urllib.request.build_opener(
            NoRedirect(),
            urllib.request.HTTPSHandler(context=context),
            urllib.request.ProxyHandler({}),
        )
        message = urllib.request.Request(request["url"], headers=request["headers"], method="GET")  # noqa: S310 - scheme checked and origin supplied only by private configuration
        try:
            with opener.open(message, timeout=request["timeout"]) as response:  # noqa: S310 - fixed validated HTTP(S) origin, redirects/proxies disabled
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    print(json.dumps({"error": "invalid_response"}))
                    return
                headers = {
                    name: response.headers.get(name, "")
                    for name in (
                        "Content-Type",
                        "X-RetailOps-Read-Mode",
                        "X-RetailOps-Source-Contract-Sha256",
                        "X-Correlation-ID",
                    )
                }
                print(
                    json.dumps(
                        {
                            "status": response.status,
                            "headers": headers,
                            "body": base64.b64encode(body).decode("ascii"),
                        }
                    )
                )
        except urllib.error.HTTPError as error:
            # Raw error bodies can contain private diagnostics; discard them.
            print(
                json.dumps(
                    {
                        "status": error.code,
                        "retry_after": (error.headers.get("Retry-After", "") or "")[:64],
                    }
                )
            )
    except Exception:  # noqa: BLE001 - only fixed error output crosses the process boundary
        print(json.dumps({"error": "unavailable"}))


if __name__ == "__main__":
    main()
