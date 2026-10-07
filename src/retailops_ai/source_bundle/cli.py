"""Explicit private-credential CLI; no model or API implicitly imports source bundles."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

from .client import BundleClientConfig, download_import


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--generated-root", type=Path, required=True)
    parser.add_argument("--require-use-case", action="append")
    args = parser.parse_args()
    try:
        descriptor = os.open(args.config, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                raise ValueError("private_configuration_required")
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("configuration_limit")
        config = BundleClientConfig.model_validate_json(raw)
        result = download_import(
            config,
            args.generated_root,
            required_use_cases=tuple(args.require_use_case or ["forecast_source"]),
        )
    except (OSError, ValueError, ImportError, TypeError, KeyError, RecursionError):
        sys.stdout.write('{"status":"failed","code":"source_bundle_rejected"}\n')
        return 1
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
