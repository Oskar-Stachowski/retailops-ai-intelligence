"""Real loopback HTTP checks streaming artifacts and bounded transport failures."""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from retailops_ai.model_lifecycle import v12_mlflow as tracking
from retailops_ai.model_lifecycle.v12_evidence import V12ArtifactReceipt


@pytest.fixture
def server():
    artifacts = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_PUT(self):
            size = int(self.headers["Content-Length"])
            artifacts[self.path] = self.rfile.read(size)
            if len(artifacts[self.path]) != size:
                return
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            if self.path == "/missing":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(artifacts.get(self.path, b'{"experiment_id":"1"}'))

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as http_server:
        port = http_server.server_port
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        try:
            yield tracking.LocalTracking(port), artifacts
        finally:
            http_server.shutdown()
            thread.join(timeout=5)


def test_artifact_stream_roundtrip_and_local_remote_corruption(server, tmp_path):
    client, artifacts = server
    raw = b"bounded-original-checkpoint" * 60000
    source = tmp_path / "payload.tar.gz"
    source.write_bytes(raw)
    receipt = V12ArtifactReceipt(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    client.upload("/artifacts/payload.tar.gz", tmp_path, source.name, receipt)
    assert artifacts["/artifacts/payload.tar.gz"] == raw
    assert client.checksum("/artifacts/payload.tar.gz", len(raw)) == (len(raw), receipt.sha256)
    with pytest.raises(ValueError, match="remote_byte_budget"):
        client.checksum("/artifacts/payload.tar.gz", 1)
    source.write_bytes(raw[:-10])
    with pytest.raises(ValueError, match="source_changed"):
        client.upload("/truncated", tmp_path, source.name, receipt)


def test_json_api_missing_resource_and_response_budget(server, monkeypatch):
    client, _ = server
    assert client.api("/json") == {"experiment_id": "1"}
    assert client.api("/echo", {"experiment_id": "1"}) == {"experiment_id": "1"}
    with pytest.raises(ValueError, match="resource_missing"):
        client.api("/missing")
    monkeypatch.setattr(tracking, "MAX_RESPONSE", 2)
    with pytest.raises(ValueError, match="response_budget"):
        client.api("/json")
