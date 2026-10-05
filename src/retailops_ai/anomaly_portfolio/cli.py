"""Public-input-only fit and saved-artifact validation scoring; no truth reader."""

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from retailops_ai.anomaly_detectors.contract import Feature, FitPolicy
from retailops_ai.anomaly_detectors.protocol import Scope, Window, series_key
from retailops_ai.anomaly_portfolio.artifacts import immutable, immutable_json
from retailops_ai.anomaly_portfolio.inputs import VerifiedFeatures, verified_features
from retailops_ai.anomaly_portfolio.model import EventCapacity, load, score
from retailops_ai.anomaly_portfolio.protocol import PortfolioProtocol
from retailops_ai.source_snapshot.files import canonical_json, decode_json, read_bytes

RECIPES: tuple[tuple[str, tuple[Feature, ...]], ...] = (
    ("full", FitPolicy().features),
    ("residual", ("residual_units", "robust_scale_units", "standardized_residual")),
    ("context", ("standardized_residual", "promotion_offered", "on_hand")),
)
PROFILES = {
    "ai-07-portfolio-v1": (128, 8, 3, 2),
    "ai-07-portfolio-v2": (128, 8, 2, 2),
    "ai-07-portfolio-v3": (128, 12, 2, 2),
}


def prepared(path: Path) -> VerifiedFeatures:
    receipt = decode_json(read_bytes(path.parent, path.name, 1024**2))
    if receipt.get("status") != "passed" or receipt.get("truth_access") != "excluded":
        raise ValueError("anomaly_portfolio_preparation_unqualified")
    frame = verified_features(Path(receipt["feature_dir"]), *(Path(p) for p in receipt["parents"]))
    if (
        frame.manifest_sha256 != receipt["feature_manifest_sha256"]
        or frame.manifest.qualified_anomaly_input_id != receipt["feature_id"]
        or frame.manifest.descriptor.rows_sha256 != receipt["feature_rows_sha256"]
        or frame.manifest.descriptor.coverage.descriptor.source_dataset_id
        != receipt["source_dataset_id"]
    ):
        raise ValueError("anomaly_portfolio_preparation_changed")
    return frame


def protocol(frame: VerifiedFeatures) -> PortfolioProtocol:
    points = frame.points()
    start = min(p.business_date for p in points)
    scopes = tuple(
        Scope.model_validate(dict(zip(Scope.model_fields, key, strict=True)))
        for key in sorted({series_key(p) for p in points})
    )

    def window(first: int, last: int) -> Window:
        return Window(start=start + timedelta(days=first), end=start + timedelta(days=last))

    return PortfolioProtocol(
        scopes=scopes,
        train=window(28, 59),
        validation=window(64, 95),
        test=window(100, 127),
        training_cutoff=datetime.combine(start + timedelta(days=63), datetime.min.time(), UTC),
        selection_cutoff=datetime.combine(start + timedelta(days=99), datetime.min.time(), UTC),
    )


def fit(args: argparse.Namespace) -> int:
    from retailops_ai.anomaly_portfolio.training import train

    receipt = decode_json(
        read_bytes(args.prepared_receipt.parent, args.prepared_receipt.name, 1024**2)
    )
    public = decode_json(
        read_bytes(Path(receipt["parents"][3]) / "snapshot", "snapshot_manifest.json", 8 * 1024**2)
    )
    parameters = public["source"]["descriptor"]["resolved_parameters"]
    if (
        parameters["profile"] not in PROFILES
        or parameters["seed"] != 42
        or tuple(parameters[k] for k in ("days", "products", "stores", "warehouses"))
        != PROFILES.get(parameters["profile"])
    ):
        raise ValueError("anomaly_portfolio_development_profile_only")
    frame = prepared(args.prepared_receipt)
    split = protocol(frame)
    immutable_json(args.output / "protocol.json", split.model_dump(mode="json"))
    models = []
    recipes = (
        (
            (
                "multiscale",
                (
                    "standardized_residual",
                    "short_count_residual",
                    "long_count_residual",
                    "inventory_shortfall",
                ),
            ),
            ("full", FitPolicy().features),
            (
                "multiscale_context",
                (
                    "standardized_residual",
                    "promotion_offered",
                    "on_hand",
                    "short_count_residual",
                    "long_count_residual",
                    "inventory_shortfall",
                ),
            ),
        )
        if args.multiscale or args.count_rate
        else RECIPES
    )
    if (args.multiscale or args.count_rate) and parameters["profile"] != "ai-07-portfolio-v3":
        raise ValueError("anomaly_multiscale_requires_new_confirmatory_profile")
    for feature_name, features in recipes:
        configurations = (
            ((0.075, 0.01), (0.10, 0.01), (0.10, 0.025))
            if args.count_rate
            else ((0.025, None), (0.05, None), (0.10, None))
        )
        for fraction, return_fraction in configurations:
            policy = FitPolicy(
                version="anomaly-detector-fit-2.0.0"
                if args.multiscale or args.count_rate
                else "anomaly-detector-fit-1.0.0",
                features=features,
                validation_alert_fraction=fraction,
                validation_high_fraction=0.005 if args.count_rate else min(0.01, fraction),
            )
            capacities = (
                (
                    EventCapacity(
                        event_type="sale_completed", alert_fraction=fraction, high_fraction=0.005
                    ),
                    EventCapacity(
                        event_type="return_completed",
                        alert_fraction=return_fraction,
                        high_fraction=0.001,
                    ),
                )
                if return_fraction is not None
                else None
            )
            started = datetime.now(UTC)
            model, resources = train(
                frame,
                split,
                policy,
                multiscale=args.multiscale,
                event_capacities=capacities,
            )
            raw = canonical_json(model.model_dump(mode="json")) + b"\n"
            directory = args.output / "models" / model.detector_id
            immutable(directory / "model.json", raw, maximum=8 * 1024**2)
            digest = hashlib.sha256(raw).hexdigest()
            decoded = load(directory / "model.json", digest)
            if decoded != model:
                raise ValueError("anomaly_portfolio_model_roundtrip")
            for family in ("seasonal_residual", "isolation_forest"):
                decisions = score(
                    decoded,
                    list(frame.points()),
                    split.scopes,
                    split.validation,
                    family,
                    "validation",
                    split.selection_cutoff,
                )
                direct = score(
                    model,
                    list(frame.points()),
                    split.scopes,
                    split.validation,
                    family,
                    "validation",
                    split.selection_cutoff,
                )
                if decisions != direct:
                    raise ValueError("anomaly_portfolio_saved_prediction_mismatch")
                immutable(
                    directory / (family + ".validation.jsonl"),
                    b"".join(canonical_json(d.model_dump(mode="json")) + b"\n" for d in decisions),
                )
            record = {
                "detector_id": model.detector_id,
                "model_sha256": digest,
                "recipe": feature_name,
                "policy": policy.model_dump(mode="json"),
                "model_path": str(directory / "model.json"),
                "resources": resources,
                "original_started_at": started.isoformat(),
                "original_completed_at": datetime.now(UTC).isoformat(),
            }
            # Times describe the original run and are never replaced on retries.
            run_path = directory / "fit_run.json"
            if run_path.exists():
                previous = decode_json(read_bytes(directory, run_path.name, 1024**2))
                if any(
                    previous[k] != record[k]
                    for k in ("detector_id", "model_sha256", "recipe", "policy")
                ):
                    raise ValueError("anomaly_portfolio_fit_run_conflict")
                record = previous
            else:
                immutable_json(run_path, record)
            models.append(record)
            print(
                json.dumps(
                    {
                        "stage": "fitted",
                        "recipe": feature_name,
                        "fraction": fraction,
                        "return_fraction": return_fraction,
                        "detector_id": model.detector_id,
                    }
                ),
                flush=True,
            )
    immutable_json(
        args.output / "fit_manifest.json",
        {
            "version": "anomaly-portfolio-fit-1.0.0",
            "models": models,
            "training_source": frame.manifest.descriptor.coverage.descriptor.source_dataset_id,
            "truth_access": "excluded",
            "final_test": "not_scored",
        },
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    recipe = parser.add_mutually_exclusive_group()
    recipe.add_argument("--multiscale", action="store_true")
    recipe.add_argument("--count-rate", action="store_true")
    return fit(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
