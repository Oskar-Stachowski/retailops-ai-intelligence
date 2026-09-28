"""Generate or check reviewed schemas/examples, without updating fixtures in CI."""

import argparse
import json
from pathlib import Path

from intelligence_examples import build_examples

from retailops_ai.data_contracts.registry import MODELS

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "contracts/intelligence/v1"


def artifacts() -> dict[str, object]:
    examples = build_examples()
    result: dict[str, object] = {}
    for family, model in MODELS.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:intelligence:{family}:1.0"
        result[f"{family}.v1.schema.json"] = schema
        result[f"{family}.v1.example.json"] = examples[family]
    result["registry.json"] = {
        "schema_version": "1.0",
        "draft": "2020-12",
        "supported_versions": ["1.0"],
        "compatibility": "exact-version; unknown fields rejected; no implicit migration",
        "semantic_validator": "retailops-ai contract-check FAMILY PATH",
        "families": sorted(MODELS),
        "fixture_kind": "synthetic metadata; no physical data or trained model",
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=CONTRACT_ROOT)
    args = parser.parse_args()
    stale = []
    for name, value in artifacts().items():
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path = args.output / name
        if args.check:
            if not path.is_file() or path.read_text() != text:
                stale.append(name)
        else:
            args.output.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if stale:
        print("Contract snapshots differ: " + ", ".join(sorted(stale)))
        return 1
    print("Intelligence contract snapshots checked." if args.check else "Contracts generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
