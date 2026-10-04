"""Pin and generate the bounded source client from executable RetailOps OpenAPI."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/retailops_ai/source_rest"
SOURCE_PATH = "services/api/app/contracts/source-reads-v2/openapi.json"
QUERY_NAMES = {
    "products": "ProductsQuery",
    "sales": "SalesQuery",
    "inventory-snapshots": "InventoryQuery",
    "forecasts": "ForecastsQuery",
    "inventory-risks": "RisksQuery",
}


def annotation(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "const" in schema:
        return "Literal[" + repr(schema["const"]) + "]"
    if "enum" in schema:
        return "Literal[" + ", ".join(repr(x) for x in schema["enum"]) + "]"
    if "anyOf" in schema:
        return " | ".join(annotation(x) for x in schema["anyOf"])
    kind = schema.get("type")
    if kind == "array":
        return "list[" + annotation(schema["items"]) + "]"
    if kind == "string":
        return {"date": "date", "date-time": "AwareDatetime", "uuid": "UUID"}.get(
            schema.get("format", ""), "str"
        )
    if kind in ("integer", "number", "boolean", "null"):
        return {"integer": "int", "number": "float", "boolean": "bool", "null": "None"}[kind]
    raise ValueError("Unsupported source schema shape")


def field(name: str, schema: dict[str, Any], required: bool) -> str:
    constraints: dict[str, Any] = {}
    shape = schema
    if "anyOf" in schema:
        shape = next(x for x in schema["anyOf"] if x.get("type") != "null")
    for original, target in {
        "minimum": "ge",
        "maximum": "le",
        "exclusiveMinimum": "gt",
        "exclusiveMaximum": "lt",
        "minLength": "min_length",
        "maxLength": "max_length",
        "pattern": "pattern",
        "minItems": "min_length",
        "maxItems": "max_length",
    }.items():
        if original in shape:
            constraints[target] = shape[original]
    default = "..." if required else repr(schema.get("default"))
    args = [default, *(k + "=" + repr(v) for k, v in constraints.items())]
    return "    " + name + ": " + annotation(schema) + " = Field(" + ", ".join(args) + ")"


def render(contract: dict[str, Any]) -> str:
    schemas = contract["components"]["schemas"]
    lines = [
        '"""Generated from pinned source OpenAPI; regenerate, do not hand edit."""',
        "from __future__ import annotations",
        "",
        "from datetime import date, timedelta",
        "from typing import Literal, Self",
        "from uuid import UUID",
        "",
        "from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator",
        "",
        "",
        "class ReadModel(BaseModel):",
        '    model_config = ConfigDict(extra="allow", frozen=True, strict=True, allow_inf_nan=False, hide_input_in_errors=True)',
        "",
        "",
        "class QueryModel(BaseModel):",
        '    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)',
        "",
        '    @model_validator(mode="after")',
        "    def period(self) -> Self:",
        '        for left, right in (("sold_from", "sold_to"), ("recorded_from", "recorded_to"), ("date_from", "date_to")):',
        "            start = getattr(self, left, None)",
        "            end = getattr(self, right, None)",
        "            if start is not None and end is not None and (end < start or end - start > timedelta(days=90)):",
        '                raise ValueError("period_outside_90_day_bound")',
        "        return self",
        "",
    ]
    done: set[str] = set()
    for name, schema in sorted(schemas.items()):
        if "enum" in schema:
            lines.extend(["", name + " = " + annotation(schema)])
            done.add(name)
    needed: set[str] = set()

    def refs(value: Any) -> set[str]:
        result: set[str] = set()
        if isinstance(value, dict):
            if "$ref" in value:
                result.add(value["$ref"].rsplit("/", 1)[-1])
            for child in value.values():
                result.update(refs(child))
        elif isinstance(value, list):
            for child in value:
                result.update(refs(child))
        return result

    for operations in contract["paths"].values():
        schema = (
            operations["get"]
            .get("responses", {})
            .get("200", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        needed.update(refs(schema))
    while any(name not in done for name in needed):
        pending = needed - done
        for name in list(pending):
            needed.update(refs(schemas[name]))
        ready = sorted(name for name in needed - done if refs(schemas[name]) <= done)
        if not ready:
            raise ValueError("Unsupported cyclic source schema")
        for name in ready:
            schema = schemas[name]
            lines.extend(["", "", "class " + name + "(ReadModel):"])
            required = schema.get("required", [])
            lines.extend(field(k, v, k in required) for k, v in schema["properties"].items())
            done.add(name)
    for resource, name in QUERY_NAMES.items():
        params = contract["paths"]["/integration/v2/" + resource]["get"]["parameters"]
        lines.extend(["", "", "class " + name + "(QueryModel):"])
        lines.extend(field(p["name"], p["schema"], p.get("required", False)) for p in params)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path)
    parser.add_argument("--source-commit")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    PACKAGE.mkdir(parents=True, exist_ok=True)
    contract_path = PACKAGE / "upstream.openapi.json"
    pin_path = PACKAGE / "upstream.json"
    if args.source_repo is not None:
        if not args.source_commit or not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
            parser.error("A full immutable source commit is required")
        raw = subprocess.run(  # noqa: S603 - fixed read-only Git command and full validated commit
            [
                shutil.which("git") or "/usr/bin/git",
                "-C",
                str(args.source_repo),
                "show",
                args.source_commit + ":" + SOURCE_PATH,
            ],
            check=True,
            capture_output=True,
        ).stdout  # noqa: S603 - fixed read-only git command
        pin = {
            "repository": "Oskar-Stachowski/retailops-cloud-native-platform",
            "commit": args.source_commit,
            "path": SOURCE_PATH,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "read_mode": "bounded_live",
            "snapshot_supported": False,
        }
    else:
        raw = contract_path.read_bytes()
        pin = json.loads(pin_path.read_text())
    if hashlib.sha256(raw).hexdigest() != pin["sha256"]:
        parser.exit(1, "Source OpenAPI pin drift\n")
    generated = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--stdin-filename", "wire.py", "-"],
        input=render(json.loads(raw)).encode(),
        capture_output=True,
        check=True,
    ).stdout  # noqa: S603 - pinned formatter and generated input
    for path, content in (
        (contract_path, raw),
        (pin_path, (json.dumps(pin, sort_keys=True, separators=(",", ":")) + "\n").encode()),
        (PACKAGE / "wire.py", generated),
    ):
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                parser.exit(1, "Generated source client contract drift\n")
        else:
            path.write_bytes(content)
    print(
        "Pinned source REST contract verified"
        if args.check
        else "Pinned source REST contract generated"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
