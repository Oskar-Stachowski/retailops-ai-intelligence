"""Read-only inspection and validation of agent tools; no execution or cloud calls."""

import argparse
import json
import sys

from retailops_ai.agent.chat_config import load_chat_config
from retailops_ai.agent.execution import READ_CAPABILITIES
from retailops_ai.agent.graph_config import load_graph_config
from retailops_ai.agent.tools import INPUT, OUTPUT, REQUEST_MODELS, ToolPolicy
from retailops_ai.security.local import strict_json


def run_agent_command(args: argparse.Namespace) -> int:
    if args.command == "agent-evaluate":
        from retailops_ai.agent.evaluation import evaluate_sync, load_evaluation

        try:
            config = load_graph_config(args.config)
            suite, release = load_evaluation(
                args.golden, args.release, config, args.rag_golden, args.lock
            )
            report = evaluate_sync(suite, release, config)
            if args.output is not None:
                with args.output.open("x", encoding="utf-8") as output:
                    output.write(report.model_dump_json(indent=2) + "\n")
        except (OSError, ValueError, TimeoutError, RecursionError):
            print("agent_evaluation_failed", file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "status": report.status,
                    "release_id": report.release_id,
                    "fixture_only": True,
                    "aws_executed": False,
                    "case_pass": report.metrics["case_pass"].model_dump(mode="json"),
                    "critical_cases": report.metrics["critical_cases"].model_dump(mode="json"),
                    "failed_gates": report.failed_gates,
                },
                sort_keys=True,
            )
        )
        return 0 if report.status == "passed" else 1
    if args.command == "agent-graph-check":
        try:
            resolved_graph = load_graph_config(args.path)
        except ValueError:
            print("agent_graph_config_invalid", file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "status": "valid",
                    "config_id": resolved_graph.config_id,
                    "graph_version": resolved_graph.config.graph_version,
                    "provider": resolved_graph.config.chat.model.provider,
                    "provider_invoked": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "agent-config-check":
        try:
            resolved = load_chat_config(args.path)
        except ValueError:
            print("agent_chat_config_invalid", file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "status": "valid",
                    "provider": resolved.config.model.provider,
                    "config_id": resolved.config_id,
                    "provider_invoked": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "agent-tools":
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "required_role": "operator",
                    "required_capability": "assistant:query",
                    "tools": [
                        {
                            "tool": tool,
                            "capability": READ_CAPABILITIES[tool],
                            "read_only": True,
                            "request_schema_id": f"urn:retailops:agent:{tool}:request:1.0",
                            "result_schema_id": f"urn:retailops:agent:{tool}:result:1.0",
                        }
                        for tool in sorted(REQUEST_MODELS)
                    ],
                },
                sort_keys=True,
            )
        )
        return 0
    try:
        maximum = 16384 if args.direction == "request" else 131072
        with args.path.open("rb") as source:
            raw = source.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError("agent_contract_too_large")
        strict_json(raw)
        if args.direction == "policy":
            ToolPolicy.model_validate_json(raw)
        else:
            (INPUT if args.direction == "request" else OUTPUT).validate_json(raw)
    except (OSError, ValueError, RecursionError):
        print("agent_tool_contract_invalid", file=sys.stderr)
        return 2
    print(json.dumps({"status": "valid", "direction": args.direction}, sort_keys=True))
    return 0
