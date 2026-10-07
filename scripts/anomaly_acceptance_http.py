"""Exercise the installed ASGI service through an owned loopback socket without test extras."""

import http.client
import json as jsonlib
import socket
import threading
import time
from types import TracebackType
from typing import Any, Self

import uvicorn
from fastapi import FastAPI


class Response:
    def __init__(self, status: int, raw: bytes) -> None:
        self.status_code = status
        self.text = raw.decode("utf-8")

    def json(self) -> dict[str, Any]:
        value = jsonlib.loads(self.text)
        if not isinstance(value, dict):
            raise ValueError("anomaly_acceptance_http_object_required")
        return value


class HTTPClient:
    def __init__(self, app: FastAPI) -> None:
        self.app = app

    def __enter__(self) -> Self:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="error", access_log=False))
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True
        )
        self.thread.start()
        until = time.monotonic() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() >= until:
                self.server.should_exit = True
                self.thread.join(timeout=10)
                self.socket.close()
                raise ValueError("anomaly_acceptance_owned_http_start_failed")
            time.sleep(0.05)
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=30)
        self.socket.close()
        if self.thread.is_alive():
            raise ValueError("anomaly_acceptance_owned_http_stop_failed")

    def request(
        self, method: str, path: str, headers: dict[str, str] | None, body: dict[str, Any] | None
    ) -> Response:
        if not path.startswith("/") or "\r" in path or "\n" in path:
            raise ValueError("anomaly_acceptance_http_path")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.request(
                method,
                path,
                jsonlib.dumps(body) if body is not None else None,
                {"Content-Type": "application/json", **(headers or {})},
            )
            result = connection.getresponse()
            raw = result.read(8 * 1024**2 + 1)
            if len(raw) > 8 * 1024**2:
                raise ValueError("anomaly_acceptance_http_response_budget")
            return Response(result.status, raw)
        finally:
            connection.close()

    def get(self, path: str, *, headers: dict[str, str] | None = None) -> Response:
        return self.request("GET", path, headers, None)

    def post(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Response:
        return self.request("POST", path, headers, json)
