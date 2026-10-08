"""Actual Assistant SQL/outbox with an explicit scripted LLM and native SQL read."""

import argparse
import hashlib
import json
import os
from pathlib import Path

import boto3
import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, text
from test_agent_graph import PolicyFixture
from test_assistant import headers, setup

from retailops_ai.adapters.native_operations_tool import (
    NativeOperationsTool,
    PostgresNativeOperationsReader,
)
from retailops_ai.agent.execution import ToolExecutor
from retailops_ai.agent.graph import GraphRunner
from retailops_ai.agent.graph_config import load_graph_config
from retailops_ai.agent.graph_traces import MemoryTraces
from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.migrations.runner import migrate

ROOT = Path(__file__).resolve().parents[2]


def no_aws(*args, **kwargs):
    raise AssertionError("AWS forbidden in unpaid Source acceptance")


def provision(private):
    config = json.loads((private / "connections.json").read_bytes())
    with psycopg.connect(config["admin"], autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE ROLE ai_app LOGIN PASSWORD {} SET timezone TO 'UTC'").format(
                sql.Literal(config["password"])
            )
        )
        conn.execute("CREATE DATABASE retailops_ai OWNER ai_app")
    admin = psycopg.conninfo.conninfo_to_dict(config["admin"])
    admin["dbname"] = "retailops_ai"
    with psycopg.connect(**admin, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION vector")
        conn.execute("CREATE SCHEMA ai AUTHORIZATION ai_app")
    migrate(
        Settings(
            APP_ENV="test", ARTIFACT_ROOT=str(private / "artifacts"), DATABASE_URL=config["ai"]
        )
    )


def emit(private):
    config = json.loads((private / "connections.json").read_bytes())
    path, tokens, authority, body, backend, _ = setup(private, intent="operations")
    graph = load_graph_config(ROOT / "agent/graph.fake.prepaid.v1.json")
    producer = create_engine(config["source_read"], hide_parameters=True)
    ai = create_engine(config["ai"], hide_parameters=True)
    adapter = NativeOperationsTool(PostgresNativeOperationsReader(producer, "test"), "test")
    provider = PolicyFixture(graph.config.chat.model)
    backend.native_tools = frozenset({"get_live_operations"})
    backend.runner = lambda: GraphRunner(
        ToolExecutor(
            authority,
            {"get_live_operations": adapter},
            graph.config.chat.tool_policy,
            "test",
            allow_fixtures=True,
        ),
        graph,
        provider,
        MemoryTraces(graph.config.policy),
    )
    settings = Settings(
        APP_ENV="test",
        ARTIFACT_ROOT=str(private / "artifacts"),
        API_AUTH_FILE=path,
        DATABASE_URL=config["ai"],
        ASSISTANT_SUGGESTION_OUTBOX_ENABLED=True,
    )
    try:
        with TestClient(
            create_app(settings, assistant_backend=backend), base_url="http://127.0.0.1"
        ) as client:
            response = client.post("/api/v1/assistant/queries", headers=headers(tokens), json=body)
            assert response.status_code == 200, "actual Assistant query failed"
            answer = response.json()
            assert len(answer["recommended_actions"]) == 1, "native operation review missing"
            assert (
                answer["recommended_actions"][0]["action"]
                == "Ask an operator to review scoped failed event processing."
            )
            with ai.connect() as conn:
                row = (
                    conn.execute(
                        text(
                            "SELECT document,wire_bytes,wire_sha256,status FROM ai.assistant_suggestion_outbox WHERE trace_id=:trace"
                        ),
                        {"trace": answer["trace_id"]},
                    )
                    .mappings()
                    .one()
                )
                assert row["status"] == "pending"
                wire = bytes(row["wire_bytes"])
                assert hashlib.sha256(wire).hexdigest() == row["wire_sha256"]
                assert json.loads(wire) == row["document"]
            for name, value in (("answer.json", json.dumps(answer)), ("event.json", wire.decode())):
                output = private / name
                output.write_text(value)
                output.chmod(0o600)
    finally:
        ai.dispose()
        producer.dispose()


def verify(private):
    config = json.loads((private / "connections.json").read_bytes())
    event = json.loads((private / "event.json").read_bytes())
    engine = create_engine(config["ai"], hide_parameters=True)
    try:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT status,wire_bytes,wire_sha256 FROM ai.assistant_suggestion_outbox WHERE event_id=:id"
                    ),
                    {"id": event["event_id"]},
                )
                .mappings()
                .one()
            )
            assert row["status"] == "sent"
            assert bytes(row["wire_bytes"]) == (private / "event.json").read_bytes()
    finally:
        engine.dispose()


if __name__ == "__main__":
    boto3.client = boto3.Session = no_aws
    boto3.session.Session.client = no_aws
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("provision", "emit", "verify"))
    parser.add_argument("private", type=Path)
    args = parser.parse_args()
    assert os.getenv("AI12_UNPAID_ACCEPTANCE") == "1"
    globals()[args.operation](args.private)
