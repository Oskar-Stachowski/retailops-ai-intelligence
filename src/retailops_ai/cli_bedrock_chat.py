"""Offline proposal by default; explicit capped execution writes a private durable receipt."""

import argparse
import asyncio
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TextIO


def add_chat_smoke(commands: Any) -> None:
    command = commands.add_parser(
        "bedrock-smoke",
        help="Propose a small real-chat smoke offline; --execute requires an explicit cost cap and private output.",
    )
    for name in ("config", "offline-config", "golden", "release", "rag-golden", "lock", "profile"):
        command.add_argument("--" + name, type=Path, required=True)
    command.add_argument("--execute", action="store_true")
    command.add_argument("--max-cost-usd")
    command.add_argument("--aws-profile")
    command.add_argument("--output", type=Path)


def _write(output: TextIO, report: dict[str, object]) -> None:
    output.seek(0)
    json.dump(report, output, indent=2, sort_keys=True)
    output.write("\n")
    output.truncate()
    output.flush()
    os.fsync(output.fileno())


def run_chat_smoke(args: argparse.Namespace) -> int:
    from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

    from retailops_ai.adapters.bedrock_access import inspect_model_access
    from retailops_ai.adapters.bedrock_chat import BedrockChatProvider
    from retailops_ai.agent.bedrock_smoke import (
        BedrockSmokeProfile,
        proposal,
        run_smoke,
        verify_smoke,
    )
    from retailops_ai.agent.evaluation import evaluate_sync, load_evaluation
    from retailops_ai.agent.graph_config import load_graph_config
    from retailops_ai.security.local import strict_json

    try:
        runtime = load_graph_config(args.config)
        offline = load_graph_config(args.offline_config)
        suite, release = load_evaluation(
            args.golden, args.release, offline, args.rag_golden, args.lock
        )
        with args.profile.open("rb") as source:
            raw = source.read(8193)
        if len(raw) > 8192:
            raise ValueError("smoke_profile_too_large")
        strict_json(raw)
        profile = BedrockSmokeProfile.model_validate_json(raw)
        verify_smoke(profile, suite, release, offline, runtime)
        if args.execute:
            if args.output is None or args.max_cost_usd is None:
                raise ValueError("explicit_cost_cap_and_output_required")
            cap = Decimal(args.max_cost_usd)
            if not cap.is_finite() or cap != runtime.config.chat.budget.pricing.max_smoke_cost:
                raise ValueError("smoke_cost_binding_mismatch")
        gates = evaluate_sync(suite, release, offline)
        if gates.status != "passed":
            raise ValueError("offline_gates_failed")
        report = proposal(profile, runtime)
        report["offline_gates_passed"] = True
        if args.output is None:
            print(json.dumps(report, sort_keys=True))
            return 0
        # Reserve the destination before creating a client or starting any AWS work.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            if args.execute:
                report.update(status="execution_started", reason=None, aws_executed=None)
                _write(output, report)
                access = inspect_model_access(runtime.chat, args.aws_profile)
                if access["status"] == "blocked":
                    report.update(
                        status="blocked",
                        reason=access["reason"],
                        aws_executed=True,
                        real_chat=False,
                        count_requests=0,
                        inference_requests=0,
                        estimated_or_reserved_usd="0",
                    )
                else:
                    provider = BedrockChatProvider(
                        runtime.chat, profile.circuit, profile=args.aws_profile
                    )
                    report = asyncio.run(run_smoke(profile, suite, runtime, provider))
                    report["offline_gates_passed"] = True
                report["model_access"] = access
            _write(output, report)
    except (
        OSError,
        ValueError,
        InvalidOperation,
        TimeoutError,
        RecursionError,
        BotoCoreError,
        ClientError,
    ):
        print('{"error":"bedrock_chat_smoke_failed"}', file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("status", "aws_executed", "real_chat", "scope", "ai12_closed")
            },
            sort_keys=True,
        )
    )
    return 1 if report["status"] in {"failed", "blocked"} else 0
