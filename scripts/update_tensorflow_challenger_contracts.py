"""Generate/check the frozen development challenger recipe and typed snapshots."""

import argparse
import json
from pathlib import Path

from retailops_ai.tensorflow_challenger.contract import (
    ChallengerManifest,
    ChallengerPolicy,
    ChallengerPrediction,
    Normalization,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    values = {"challenger.default.json": ChallengerPolicy().model_dump(mode="json")}
    for name, model in (
        ("challenger_policy", ChallengerPolicy),
        ("normalization", Normalization),
        ("challenger_prediction", ChallengerPrediction),
        ("challenger_manifest", ChallengerManifest),
    ):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:ai09:{name}:1.0.0"
        values[name + ".schema.json"] = schema
    for name, value in values.items():
        path = ROOT / "contracts/evaluation/v2" / name
        raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("TensorFlow challenger contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    print("TensorFlow challenger contracts checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
