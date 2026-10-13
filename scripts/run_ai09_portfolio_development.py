"""One frozen real development comparison for the approved portfolio scope.

Reuse the native Source preparation and forecast comparison implementations.
This entrypoint never generates final data, grants final access or claims ready.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
PLAN = ROOT / "docs/reference/ai09-portfolio-development-v1.json"


def frozen_plan() -> dict[str, Any]:
    plan: dict[str, Any] = json.loads(PLAN.read_bytes())
    if (
        plan["version"] != "ai09-portfolio-development-1.0.0"
        or plan["profile_name"] != "ai09-portfolio-development-1"
        or plan["generation"]["profile"] != "ai-load"
        or plan["generation"]["seed"] != 42
        or any(
            plan[name] is not False
            for name in (
                "final_generation_authorized",
                "final_test_opened",
                "quality_qualified",
                "stage_ready",
            )
        )
        or plan["comparison"]["maximum_attempts"] != 1
    ):
        raise ValueError("portfolio_development_scope_changed")
    return plan


def require_frozen_output(output: Path) -> None:
    import measure_ai09_development_capacity as probe

    if probe.read(output / "preparation-plan.json") != frozen_plan():
        raise ValueError("portfolio_development_frozen_plan_changed")


def comparison_policy(plan: dict[str, Any]) -> Any:
    from retailops_ai.evaluation_campaign.development_contract import DevelopmentComparisonPolicy
    from retailops_ai.forecasting.functional_contract import FunctionalPolicy
    from retailops_ai.forecasting.model_contract import ModelPolicy
    from retailops_ai.tensorflow_challenger.contract import ChallengerPolicy

    options = plan["comparison"]
    return DevelopmentComparisonPolicy(
        tensorflow=ChallengerPolicy(
            epochs=options["tensorflow_epochs"],
            initialization_seed=options["initialization_seed"],
            data_seed=plan["generation"]["seed"],
        ),
        trees=FunctionalPolicy(
            model=ModelPolicy(
                fit_wall_seconds=options["tree_fit_wall_seconds"],
                fit_cpu_seconds=options["tree_fit_cpu_seconds"],
                random_state=options["initialization_seed"],
            )
        ),
    )


def forecast(output: Path) -> None:
    import measure_ai09_development_capacity as probe

    from retailops_ai.evaluation_campaign.development import run_development_comparison
    from retailops_ai.forecasting.calendar import build_calendar
    from retailops_ai.forecasting.contract import OriginWindow
    from retailops_ai.forecasting.manifest_contract import FoldPlan, SplitPolicy
    from retailops_ai.forecasting.manifests import build_feature_set
    from retailops_ai.forecasting.splits import build_split, verify_split

    plan = frozen_plan()
    curated = Path(probe.read(output / "curation.json")["destination"])
    fold = FoldPlan.model_validate_json(json.dumps(plan["comparison"]["fold"]))
    policy = comparison_policy(plan)
    probe.write(output / "comparison-policy.json", policy.model_dump(mode="json"))
    calendar = build_calendar(
        curated, OriginWindow(start=fold.train.start, end=fold.development_holdout.end)
    )
    features = build_feature_set(curated, calendar, output / "features")
    split = build_split(
        features,
        curated,
        output / "splits",
        SplitPolicy(
            folds=(fold,),
            minimum_eligible_rows_per_role=plan["comparison"]["minimum_eligible_rows_per_role"],
        ),
    )
    manifest = verify_split(split, features)
    probe.write(
        output / "forecast-parents.json",
        {
            "features": str(features),
            "split": str(split),
            "curated": str(curated),
            "split_manifest": manifest.model_dump(mode="json"),
            "final_test_opened": False,
        },
    )
    report = run_development_comparison(
        features=features,
        split=split,
        curated=curated,
        fold_name=fold.name,
        output=output / "comparison",
        policy=policy,
    )
    probe.write(output / "forecast-completed.json", report)


def worker(args: argparse.Namespace) -> None:
    import ai09_preparation_worker as native
    import measure_ai09_development_capacity as probe

    require_frozen_output(args.output)
    if args.operation == "native":
        native.run(argparse.Namespace(source=args.source, output=args.output, worker=args.phase))
    elif args.operation == "seal":
        import run_ai09_preparation as controller

        controller.seal_worker(args.source, args.output, args.phase)
    else:
        identity = probe.read(args.output / "preparation-identity.json")
        probe.clean_pin(ROOT, identity["consumer_commit"])
        before = native.digest(native.runtime(ROOT, producer=False))
        if before != identity["consumer_runtime_sha256"]:
            raise ValueError("portfolio_development_runtime_changed")
        forecast(args.output)
        if native.digest(native.runtime(ROOT, producer=False)) != before:
            raise ValueError("portfolio_development_runtime_changed_during_forecast")


def run(args: argparse.Namespace) -> None:
    import measure_ai09_development_capacity as probe
    import run_ai09_preparation as controller

    from retailops_ai.evaluation_campaign import preparation_execution

    probe.require_remote()
    plan = frozen_plan()
    # No resumption or new attempt is inferred from a missing observer. The
    # existing controller refuses an already present output or preflight root.
    controller.initialize(args.source, args.output, plan=plan, producer_python=args.producer_python)
    prefix = [
        "-I",
        "-B",
        str(Path(__file__).resolve()),
        "--source",
        str(args.source),
        "--output",
        str(args.output),
    ]
    outcome: dict[str, Any] = {
        "version": plan["version"],
        "profile_name": plan["profile_name"],
        "status": "running",
        "final_test_opened": False,
        "stage_ready": False,
        "scope": plan["scope"],
    }
    try:
        for phase in probe.PHASES:
            interpreter = (
                args.producer_python if phase in probe.PHASES[:3] else Path(sys.executable)
            )
            controller.phase(
                args.source,
                args.output,
                phase,
                producer_python=args.producer_python,
                native_command=[
                    str(interpreter),
                    *prefix,
                    "--operation",
                    "native",
                    "--phase",
                    phase,
                ],
                seal_command=[sys.executable, *prefix, "--operation", "seal", "--phase", phase],
            )
        preparation = controller.finalize(args.output)
        if preparation["status"] != "complete" or preparation["completed_phases"] != 5:
            raise ValueError("portfolio_development_preparation_incomplete")
        outcome["preparation"] = preparation
        preparation_execution.write_once(
            args.output / "forecast-intent.json",
            {
                "scope": plan["scope"],
                "maximum_attempts": 1,
                "preparation_events_sha256": preparation["events_sha256"],
                "final_test_opened": False,
            },
        )
        measurement = probe.monitor(
            [sys.executable, *prefix, "--operation", "forecast"],
            cwd=ROOT,
            env=controller.environment(args.output),
            log=args.output / "forecast.log",
            roots=(args.output, args.source / "data/generated/ai09-capacity-snapshot"),
            budgets=plan["budgets"],
            deadline=time.perf_counter()
            + plan["budgets"]["wall_seconds"]
            - preparation["charged_wall_seconds"],
        )
        outcome["forecast_resources"] = measurement
        if measurement["status"] != "passed":
            raise ValueError("portfolio_development_forecast_failed")
        completed = probe.read(args.output / "forecast-completed.json")
        if completed.get("status") != "completed_development_diagnostic":
            raise ValueError("portfolio_development_comparison_incomplete")
        outcome.update(
            status="completed_development_comparison", comparison_id=completed["comparison_id"]
        )
    except BaseException as error:
        outcome.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        probe.write(args.output / "portfolio-development-result.json", outcome)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--producer-python", type=Path)
    parser.add_argument("--operation", choices=("run", "native", "seal", "forecast"), default="run")
    parser.add_argument(
        "--phase", choices=("generation", "qualification", "export", "import", "curation")
    )
    args = parser.parse_args()
    if args.operation == "run" and args.producer_python is None:
        parser.error("--producer-python required for run")
    if args.operation in {"native", "seal"} and args.phase is None:
        parser.error("--phase required for native/seal")
    # The Source worker imports only its native dependencies. Consumer operations
    # must load this checkout, including under an isolated interpreter (-I).
    if args.operation != "native" or args.phase in {"import", "curation"}:
        sys.path.insert(0, str(ROOT / "src"))
    run(args) if args.operation == "run" else worker(args)


if __name__ == "__main__":
    main()
