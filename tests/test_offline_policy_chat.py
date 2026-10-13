"""Scripted rendering must still pass independent document evidence oracles."""

import asyncio
from datetime import datetime
from functools import partial

import pytest
from test_document_evidence import CONFIG, SUITE, case

from retailops_ai.adapters.offline_policy_chat import OfflinePolicyChat
from retailops_ai.agent.evaluation import RecordedTools, fixture_authority
from retailops_ai.agent.execution import ToolExecutor
from retailops_ai.agent.graph import GraphRunner
from retailops_ai.agent.graph_config import load_graph_config
from retailops_ai.agent.graph_traces import MemoryTraces


@pytest.mark.parametrize("number", range(1, 7))
def test_scripted_documents_match_frozen_independent_answers(number):
    value = case(number)
    graph = load_graph_config(CONFIG)
    authority, bearer = fixture_authority(value)
    adapter = RecordedTools(value)
    runner = GraphRunner(
        ToolExecutor(
            authority,
            {row.request.tool: adapter for row in value.tools},
            graph.config.chat.tool_policy,
            "test",
            allow_fixtures=True,
            clock=partial(datetime.fromisoformat, value.decision_time),
        ),
        graph,
        OfflinePolicyChat(graph.config.chat.model),
        MemoryTraces(graph.config.policy),
        pin=SUITE.pin,
    )
    result = asyncio.run(runner.run_json(bearer, value.request_json))
    assert result.status == "succeeded", result.model_dump_json()
    assert result.answer == value.expected.answer
