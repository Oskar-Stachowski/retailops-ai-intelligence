"""Run a qualified registry decision in the checkout's isolated AI container."""

import argparse
import json
import os
import sys
from pathlib import Path

import local_stack as stack

from retailops_ai.model_lifecycle.contracts import Request
from retailops_ai.security.model_operator import model_operator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("review", "register", "reject", "promote", "rollback"))
    parser.add_argument("--run-id")
    parser.add_argument("--version")
    parser.add_argument("--evidence-id")
    parser.add_argument("--qualification-sha256")
    parser.add_argument("--decision-id")
    parser.add_argument("--reason")
    parser.add_argument("--image-digest")
    parser.add_argument("--policy-file", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        # Authenticate before launching a container. Its entry point authenticates again.
        model_operator(args.policy_file, args.credentials_file)
        request = (
            Request.model_validate_json(
                json.dumps(
                    {
                        "action": args.action,
                        "mlflow_run_id": args.run_id,
                        "model_version": args.version,
                        "evidence_id": args.evidence_id,
                        "qualification_sha256": args.qualification_sha256,
                        "decision_id": args.decision_id,
                        "reason": args.reason.strip() if args.reason else None,
                        "image_digest": args.image_digest,
                    }
                )
            )
            if args.action != "review"
            else None
        )
        mounts: list[str] = []
        for source, destination in (
            (args.policy_file, "policy.json"),
            (args.credentials_file, "credential.json"),
        ):
            path = source.resolve(strict=True)
            if (
                path.is_symlink()
                or not path.is_relative_to(stack.ROOT / ".local")
                or ":" in str(path)
            ):
                raise ValueError("operator_file_must_be_private_checkout_local")
            mounts.extend(("--volume", str(path) + ":/run/model-operator/" + destination + ":ro"))
        result = stack.compose_command(
            stack.environment_file(create=False),
            "run",
            "--rm",
            "-T",
            "--user",
            str(os.geteuid()),
            *mounts,
            "api-migrate",
            "python",
            "-m",
            "retailops_ai.model_lifecycle.cli",
            "--policy-file",
            "/run/model-operator/policy.json",
            "--credentials-file",
            "/run/model-operator/credential.json",
            *(["--review"] if args.action == "review" else []),
            stdin=request.model_dump_json()
            if request
            else json.dumps({"model_version": args.version}),
        )
        print(result)
        return 0
    except (OSError, ValueError, RuntimeError):
        print('{"error":"model_lifecycle_operation_failed"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
