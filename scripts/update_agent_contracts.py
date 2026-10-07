"""Freeze the eight typed tool interfaces and explicitly synthetic examples."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from retailops_ai.agent.execution import READ_CAPABILITIES
from retailops_ai.agent.native_forecast import NativeForecastRead
from retailops_ai.agent.tools import (
    INPUT,
    OUTPUT,
    REQUEST_MODELS,
    RESULT_MODELS,
    NativeInventoryEvidence,
    QualifiedSalesEvidence,
    ToolPolicy,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "contracts/agent/v1"


def examples() -> dict[str, tuple[dict[str, object], dict[str, object]]]:
    as_of = "2026-08-22T23:59:59Z"
    scope = {"product_ids": ["p-101"], "selling_location_ids": ["s-03"], "channel": "store"}
    key = {"product_id": "p-101", "selling_location_id": "s-03", "channel": "store"}
    window = {"start": "2026-08-16", "end": "2026-08-22"}
    result: dict[str, tuple[dict[str, object], dict[str, object]]] = {}
    payloads = {
        "get_sales_summary": {
            **key,
            "window": window,
            "observed_sales_units": 8.0,
            "unit_of_measure": "unit",
        },
        "get_inventory_status": {
            **key,
            "stock_location_id": "warehouse-01",
            "mapping_ref": "fixture-location-mapping-v1",
            "physical_units": 12.0,
            "reserved_units": 2.0,
            "available_units": 10.0,
            "unit_of_measure": "unit",
        },
        "get_stockout_risk": {
            **key,
            "window": {"start": "2026-08-23", "end": "2026-08-29"},
            "probability": 0.6,
            "threshold": 0.7,
            "calibrated": True,
            "model_id": "model-sha256-" + "a" * 64,
            "model_release_ref": "fixture-calibrated-risk-release-v1",
            "inventory_source_ref": "fixture-inventory-v1",
            "inventory_as_of": as_of,
        },
        "get_detected_anomalies": {
            **key,
            "window": window,
            "expected_units": 10.0,
            "observed_units": 8.0,
            "detector_version": "fixture-detector-v1",
            "model_release_ref": "fixture-detector-release-v1",
        },
        "get_live_operations": {**key, "stream_status": "healthy", "lag_seconds": 2.0},
        "get_model_status": {
            **key,
            "model_id": "model-sha256-" + "a" * 64,
            "deployed_release_ref": "fixture-deployed-release-v1",
            "deployment_environment": "test",
            "alias": "fixture-champion",
            "evaluation_ref": "fixture-evaluation-v1",
            "approved": True,
        },
    }
    for tool, item in payloads.items():
        request: dict[str, object] = {
            "schema_version": "1.0",
            "contract_type": "tool_request",
            "tool": tool,
            "scope": scope,
            "as_of": as_of,
            "limit": 20,
        }
        if tool in {"get_sales_summary", "get_detected_anomalies", "get_stockout_risk"}:
            request["window"] = item["window"]
        if tool == "get_sales_summary":
            request["grain"] = "product_selling_location_channel_period"
        output: dict[str, object] = {
            "schema_version": "1.0",
            "contract_type": "agent_tool_result",
            "tool": tool,
            "status": "ok",
            "as_of": as_of,
            "freshness_status": "current",
            "source_ref": "fixture-" + tool,
            "items": [item],
            "error": None,
            "source_kind": "fixture",
        }
        result[tool] = request, output
    forecast_request = json.loads(
        (ROOT / "contracts/intelligence/v1/tool_request.v1.example.json").read_text()
    )
    forecast = deepcopy(
        json.loads((ROOT / "contracts/intelligence/v1/tool_result.v1.example.json").read_text())
    )
    # Freshness is synthetic scenario input here; original intelligence examples stay unchanged.
    forecast["freshness_status"] = "current"
    for item in forecast["items"]:
        item["freshness_status"] = "current"
    result["get_demand_forecast"] = (
        forecast_request,
        {
            "schema_version": "1.0",
            "contract_type": "agent_tool_result",
            "tool": "get_demand_forecast",
            "result": forecast,
            "source_kind": "fixture",
        },
    )
    result["search_knowledge"] = (
        {
            "schema_version": "1.0",
            "contract_type": "tool_request",
            "tool": "search_knowledge",
            "retrieval": {
                "schema_version": "1.0",
                "question": "What is verified?",
                "purpose": "verified_state",
            },
        },
        {
            "schema_version": "1.0",
            "contract_type": "agent_tool_result",
            "tool": "search_knowledge",
            "status": "no_data",
            "index_id": "index-sha256-" + "a" * 64,
            "pin_generation": 1,
            "provider": "bedrock",
            "retrieval_config_id": "retrieval-config-sha256-" + "b" * 64,
            "content_trust": "untrusted_reference",
            "items": [],
            "context_tokens": 0,
            "context_bytes": 0,
            "source_kind": "fixture",
        },
    )
    return result


def artifacts() -> dict[str, object]:
    result: dict[str, object] = {}
    cases = examples()
    catalogue = []
    for tool in sorted(REQUEST_MODELS):
        request, output = cases[tool]
        INPUT.validate_json(json.dumps(request))
        OUTPUT.validate_json(json.dumps(output))
        for direction, model in (
            ("request", REQUEST_MODELS[tool]),
            ("result", RESULT_MODELS[tool]),
        ):
            schema = model.model_json_schema()
            schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
            schema["$id"] = f"urn:retailops:agent:{tool}:{direction}:1.0"
            result[f"{tool}.{direction}.v1.schema.json"] = schema
        result[f"{tool}.request.v1.example.json"] = request
        result[f"{tool}.result.v1.example.json"] = output
        catalogue.append(
            {
                "tool": tool,
                "capability": READ_CAPABILITIES[tool],
                "read_only": True,
                "request_schema": f"{tool}.request.v1.schema.json",
                "result_schema": f"{tool}.result.v1.schema.json",
                "runtime_adapter": (
                    "pinned_postgres_knowledge"
                    if tool == "search_knowledge"
                    else "native_v12_forecast_reader"
                    if tool == "get_demand_forecast"
                    else "verified_source_curated_raw_dq_day_sales"
                    if tool == "get_sales_summary"
                    else "verified_source_curated_physical_inventory"
                    if tool == "get_inventory_status"
                    else "not_implemented_requires_upstream_stages"
                ),
            }
        )
    result["catalogue.json"] = {
        "schema_version": "1.0",
        "required_role": "operator",
        "required_capability": "assistant:query",
        "fixture_kind": "Synthetic data and metadata; no trained model, business serving, AWS invocation or semantic acceptance",
        "tools": catalogue,
    }
    result["tool-policy.v1.schema.json"] = ToolPolicy.model_json_schema() | {
        "$schema": "https://json-schema.org/draft/2020-12/schema"
    }
    result["tool-policy.v1.example.json"] = ToolPolicy(
        schema_version="1.0", profile="agent-tools-bounded-v1"
    ).model_dump(mode="json")
    result["native-forecast-read.v1.schema.json"] = NativeForecastRead.model_json_schema() | {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:retailops:agent:native-forecast-read:1.0",
    }
    result["qualified-sales-evidence.v1.schema.json"] = (
        QualifiedSalesEvidence.model_json_schema()
        | {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:retailops:agent:qualified-sales-evidence:1.0",
        }
    )
    result["native-inventory-evidence.v1.schema.json"] = (
        NativeInventoryEvidence.model_json_schema()
        | {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:retailops:agent:native-inventory-evidence:1.0",
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for name, value in artifacts().items():
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path = CONTRACT_ROOT / name
        if args.check:
            if not path.is_file() or path.read_text() != text:
                stale.append(name)
        else:
            CONTRACT_ROOT.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if stale:
        print("Agent snapshots differ: " + ", ".join(sorted(stale)))
        return 1
    print("Agent tool contracts checked." if args.check else "Agent tool contracts generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
