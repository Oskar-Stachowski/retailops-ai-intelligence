"""Execute an initialized, frozen Project development forecast journal once."""

import argparse
import json
import logging
from pathlib import Path

from retailops_ai.evaluation_campaign.development_execution import (
    DevelopmentExecutionPaths,
    run_development_forecast,
)
from retailops_ai.source_snapshot.files import read_bytes


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--paths", required=True, type=Path)
    parser.add_argument("--maximum-new-operations", type=int, default=12, choices=range(1, 17))
    args = parser.parse_args()
    paths = DevelopmentExecutionPaths.model_validate_json(
        read_bytes(args.paths.parent, args.paths.name, 65536)
    )
    result = run_development_forecast(
        args.journal, paths, maximum_new_operations=args.maximum_new_operations
    )
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
