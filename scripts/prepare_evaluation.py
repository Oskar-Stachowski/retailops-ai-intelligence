"""Offline preparation of verified historical evaluation evidence, without DB mutation."""

import argparse
import json
from pathlib import Path

from retailops_ai.model_lifecycle.evaluation_importer import import_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.resolve().is_relative_to(args.run_dir.resolve()):
            raise ValueError("evaluation_output_inside_source")
        evidence = import_evidence(args.run_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Existing evidence is never replaced silently.
        with args.output.open("x") as stream:
            stream.write(evidence.model_dump_json() + "\n")
        print(
            json.dumps(
                dict(
                    status="verified",
                    evidence_sha256=evidence.evidence_sha256,
                    quality_status=evidence.descriptor.quality_status,
                    serving_eligible=False,
                )
            )
        )
        return 0
    except Exception:
        print('{"error":"evaluation_preparation_failed"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
