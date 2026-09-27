"""Package identity and safe offline configuration validation."""

import argparse
import json
import sys
from importlib.metadata import version
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import SettingsError

from retailops_ai.config import load_settings
from retailops_ai.contracts import ApplicationInfo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retailops-ai")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="Print package identity as JSON.")
    check = commands.add_parser("config-check", help="Validate settings without network or writes.")
    check.add_argument("--env-file", type=Path, help="Explicit dotenv file; environment wins.")
    args = parser.parse_args(argv)

    if args.command == "version":
        info = ApplicationInfo(version=version("retailops-ai-intelligence"))
        print(info.model_dump_json())
        return 0

    if args.env_file is not None and not args.env_file.is_file():
        print('{"error":"configuration_file_unavailable"}', file=sys.stderr)
        return 2
    try:
        settings = load_settings(args.env_file)
    except ValidationError as exc:
        fields = sorted(
            {
                str(error["loc"][0])
                for error in exc.errors(
                    include_url=False, include_context=False, include_input=False
                )
                if error["loc"]
            }
        )
        print(json.dumps({"error": "invalid_configuration", "fields": fields}), file=sys.stderr)
        return 2
    except (SettingsError, OSError, UnicodeError):
        print('{"error":"configuration_unavailable"}', file=sys.stderr)
        return 2

    print(json.dumps({"status": "valid", "app_env": settings.app_env}))
    return 0
