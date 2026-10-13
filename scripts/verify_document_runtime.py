"""Explicit paid HTTP/PG/Titan/Converse smoke; a two-question development check, not AI12 closure."""

import argparse
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from retailops_ai.api.app import create_app
from retailops_ai.assistant.contracts import AssistantAnswer, AssistantRun
from retailops_ai.assistant.runtime import DocumentAssistant
from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.security.local import BEARER_PATTERN


def private_token(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r") as source:
        info = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_size > 256
        ):
            raise ValueError("invalid_private_token_file")
        token = source.read(256).strip()
    if re.fullmatch(BEARER_PATTERN, token) is None:
        raise ValueError("invalid_private_token")
    return token


def save(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def smoke(args: argparse.Namespace) -> int:
    if not args.execute_aws:
        raise ValueError("explicit_aws_execution_required")
    if args.output.exists():
        raise ValueError("receipt_already_exists")
    settings = load_settings(args.env_file)
    if settings.app_env != "local" or settings.assistant_runtime_file is None:
        raise ValueError("configured_local_document_runtime_required")
    headers = {"Authorization": "Bearer " + private_token(args.token_file)}
    foreign = {"Authorization": "Bearer " + private_token(args.foreign_token_file)}
    body = json.loads(args.query_file.read_text())
    golden = json.loads(args.golden.read_text())
    app = create_app(settings)
    backend: DocumentAssistant = app.state.assistant_backend
    config = backend.runtime_config
    cases = golden["cases"]
    if len(cases) != 2 or {row["question"] for row in cases} != {
        row.question for row in config.graph.policy.document_rules
    }:
        raise ValueError("golden_routes_mismatch")
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "status": "execution_started",
        "created_at": datetime.now(UTC).isoformat(),
        "kind": "configured_http_real_postgres_titan_converse",
        "ai12_closed": False,
        "full_golden_qualified": False,
        "runtime_config_id": config.config_id(),
        "runtime_code_sha256": config.code_sha256,
        "golden_sha256": canonical_sha256(golden),
        "source_dataset_id": config.source_dataset_id,
        "snapshot_id": config.snapshot_id,
        "source_catalog_sha256": config.source_catalog_sha256,
        "source_scope": body["scope"],
        "source_data_kind": "accepted_ai03_synthetic_source_fixture",
        "index_id": backend.index_id,
        "model": config.graph.chat.model.model_dump(mode="json"),
        "chat_cap_usd": str(config.graph.chat.budget.pricing.max_smoke_cost),
        "embedding_max_requests": config.embedding_max_requests,
        "embedding_max_input_bytes": config.embedding_max_input_bytes,
        "cases": [],
    }
    # Keep a durable start receipt before any request. A cancelled/unsettled attempt
    # must retain its full campaign reservation outside this process.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save(args.output, report)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            if backend.chat_provider.provider is not None or backend.embedding_provider is not None:
                raise ValueError("provider_started_before_request")
            for case in cases:
                response = client.post(
                    "/api/v1/assistant/queries",
                    json=body | {"question": case["question"]},
                    headers=headers,
                )
                result: dict[str, Any] = {
                    "case_id": case["case_id"],
                    "http_status": response.status_code,
                    "passed": False,
                }
                report["cases"].append(result)
                if response.status_code == 200:
                    answer = AssistantAnswer.model_validate_json(response.content)
                    response_trace = client.get(
                        "/api/v1/assistant/runs/" + str(answer.trace_id), headers=headers
                    )
                    trace = AssistantRun.model_validate_json(response_trace.content)
                    hidden = client.get(
                        "/api/v1/assistant/runs/" + str(answer.trace_id), headers=foreign
                    ).status_code
                    with backend.engine.connect() as connection:
                        record = connection.execute(
                            text("""SELECT r.reserved_tokens,r.record,a.record
                                FROM ai.assistant_runs r JOIN ai.assistant_answers a USING(trace_id)
                                WHERE r.trace_id=:id"""),
                            {"id": answer.trace_id},
                        ).one()
                    checks = {
                        "expected_outcome": answer.outcome == case["expected_outcome"],
                        "one_required_citation": len(answer.citations) == 1
                        and answer.citations[0].chunk_id == case["chunk_id"]
                        and answer.citations[0].document_status == case["document_status"]
                        and answer.citations[0].source_ref.startswith(case["source_ref"] + "#"),
                        "exact_reviewed_quote": len(answer.evidence) == 1
                        and answer.evidence[0].claim.endswith(
                            "literal quote="
                            + json.dumps(case["required_quote"], ensure_ascii=False)
                        )
                        and case["fact_scope"] in answer.evidence[0].claim,
                        "no_business_actions": not answer.recommended_actions,
                        "runtime_binding": answer.agent_config_version == config.config_id()
                        and trace.agent_config_version == config.config_id()
                        and answer.index_id == backend.index_id,
                        "persisted": record[0] == 19000
                        and record[1] == trace.model_dump(mode="json")
                        and record[2] == answer.model_dump(mode="json"),
                        "trace_authorization": response_trace.status_code == 200 and hidden == 404,
                        "only_knowledge_tool": bool(trace.tools)
                        and all(row.name == "search_knowledge" for row in trace.tools),
                        "trace_succeeded": trace.status == "succeeded" and trace.error_code is None,
                    }
                    result.update(
                        checks=checks,
                        passed=all(checks.values()),
                        answer=answer.model_dump(mode="json"),
                        trace=trace.model_dump(mode="json"),
                    )
                save(args.output, report)
                if response.status_code != 200:
                    break
            report["status"] = (
                "passed"
                if len(report["cases"]) == len(cases)
                and all(row["passed"] for row in report["cases"])
                else "failed"
            )
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
    finally:
        chat, embedding = backend.chat_provider.provider, backend.embedding_provider
        report.update(
            completed_at=datetime.now(UTC).isoformat(),
            chat_estimated_or_reserved_usd=str(backend.smoke.charged),
            inference_requests=chat.inference_requests if chat else 0,
            count_requests=chat.count_requests if chat else 0,
            embedding_requests=embedding.requests if embedding else 0,
            embedding_input_tokens=embedding.input_tokens if embedding else 0,
            verified_destination_regions=list(chat.destination_regions) if chat else [],
        )
        save(args.output, report)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "inference_requests",
                    "embedding_requests",
                    "chat_estimated_or_reserved_usd",
                )
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-aws", action="store_true")
    for name in ("env-file", "token-file", "foreign-token-file", "query-file", "golden", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    try:
        return smoke(parser.parse_args())
    except Exception as exc:
        print(json.dumps({"status": "not_started", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
