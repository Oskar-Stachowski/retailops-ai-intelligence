"""Freeze proposed receiver schemas independently of SourceSnapshot and capabilities."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from retailops_ai.source_replay.wire import Capture, Envelope

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, model in (("capture", Capture), ("event", Envelope)):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        raw = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        path = ROOT / f"contracts/source_replay/v1/{name}.schema.json"
        if args.check:
            if path.read_text(encoding="utf-8") != raw:
                raise ValueError("observation_replay_contract_changed")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw, encoding="utf-8")
    print("Observation replay receiver schemas match; Source live capture is unsupported.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
