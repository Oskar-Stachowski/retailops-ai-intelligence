"""Prepare/review a local v12 inference capsule; no registry alias, DB or HTTP writes."""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from retailops_ai.forecast_jobs.inputs import verify_inputs_package
from retailops_ai.model_lifecycle.v12_development import read_development_acceptance
from retailops_ai.model_lifecycle.v12_release import approve_v12, load_approved_v12, qualify_v12
from retailops_ai.model_lifecycle.v12_release_contracts import V12ApprovalRequest
from retailops_ai.security.model_operator import model_operator
from retailops_ai.source_snapshot.files import canonical_json, read_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("qualify", "approve", "predict"):
        command = commands.add_parser(name)
        command.add_argument("--run-dir", type=Path, required=True)
        command.add_argument("--verifier-python", type=Path, required=True)
        command.add_argument("--verify-timeout-seconds", type=int, default=3600)
        if name == "qualify":
            for field in ("run-id", "cohort-id", "fold", "recipe-id"):
                command.add_argument("--" + field, required=True)
            for field in ("inputs-dir", "feature-dir", "curated-dir", "output-root"):
                command.add_argument("--" + field, type=Path, required=True)
            command.add_argument("--development-acceptance", type=Path)
            command.add_argument("--valid-seconds", type=int, default=86400)
        elif name == "approve":
            for field in (
                "qualification-dir",
                "policy-file",
                "credentials-file",
                "request-file",
                "reports-dir",
                "output-root",
            ):
                command.add_argument("--" + field, type=Path, required=True)
        else:
            command.add_argument("--release-dir", type=Path, required=True)
            command.add_argument("--release-id", required=True)
            command.add_argument("--image-digest", required=True)
            command.add_argument("--inputs-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        common = dict(verify_timeout_seconds=args.verify_timeout_seconds)
        if args.command == "qualify":
            if not 1 <= args.valid_seconds <= 604800:
                raise ValueError("v12_qualification_validity_limit")
            path = qualify_v12(
                args.run_dir,
                args.verifier_python,
                run_id=args.run_id,
                cohort_id=args.cohort_id,
                fold=args.fold,
                recipe_id=args.recipe_id,
                inputs_dir=args.inputs_dir,
                feature_dir=args.feature_dir,
                curated_dir=args.curated_dir,
                output_root=args.output_root,
                development_acceptance=read_development_acceptance(args.development_acceptance)
                if args.development_acceptance
                else None,
                valid_until=datetime.now(UTC) + timedelta(seconds=args.valid_seconds),
                **common,
            )
            result = dict(qualification_id=path.name, serving_eligible=False)
        elif args.command == "approve":
            actor = model_operator(args.policy_file, args.credentials_file)
            request = V12ApprovalRequest.model_validate_json(
                canonical_json(read_json(args.request_file.parent, args.request_file.name))
            )
            path = approve_v12(
                args.qualification_dir,
                args.run_dir,
                args.verifier_python,
                actor=actor,
                request=request,
                reports_dir=args.reports_dir,
                output_root=args.output_root,
                **common,
            )
            result = dict(
                release_id=path.name, registered_in_mlflow=False, activated_as_champion=False
            )
        else:
            loaded = load_approved_v12(
                args.release_dir,
                args.run_dir,
                args.verifier_python,
                release_id=args.release_id,
                image_digest=args.image_digest,
                **common,
            )
            prediction = loaded.predict(verify_inputs_package(args.inputs_dir))
            result = dict(
                release_id=prediction.inference.release_id,
                rows=len(prediction.predictions),
                predictions_sha256=prediction.predictions_sha256,
                model_refits=prediction.model_refits,
                published_forecast_outputs=0,
            )
        sys.stdout.buffer.write(canonical_json(result) + b"\n")
        return 0
    except Exception:
        sys.stderr.write("v12_inference_release_failed\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
