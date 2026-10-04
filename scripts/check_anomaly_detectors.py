"""Two fresh native development runs and data-only inference without training libraries."""

from __future__ import annotations

import argparse
import builtins
import hashlib
import importlib.util
import json
import resource
import subprocess
import sys
import tempfile
import time
from datetime import UTC, date, datetime
from importlib.abc import MetaPathFinder
from pathlib import Path
from typing import Any
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def extract(fixtures: Path, name: str, destination: Path) -> None:
    lineage = json.loads((fixtures / (name + ".lineage.json")).read_bytes())
    archive_path = fixtures / (name + ".zip")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != lineage["archive_sha256"]:
        raise ValueError("anomaly_detector_fixture_checksum")
    expected = {k: v for c in lineage["cases"].values() for k, v in c["files"].items()}
    with ZipFile(archive_path) as archive:
        if (
            len(archive.infolist()) != len(expected)
            or {i.filename for i in archive.infolist()} != set(expected)
            or sum(i.file_size for i in archive.infolist()) > 32 * 1024**2
            or any(
                i.filename.startswith("/") or ".." in Path(i.filename).parts
                for i in archive.infolist()
            )
        ):
            raise ValueError("anomaly_detector_unbounded_fixture")
        for item in archive.infolist():
            raw = archive.read(item)
            spec = expected[item.filename]
            if len(raw) != spec["bytes"] or hashlib.sha256(raw).hexdigest() != spec["sha256"]:
                raise ValueError("anomaly_detector_fixture_member")
        archive.extractall(destination)


def development_protocol(feature_dir: Path) -> Any:
    from retailops_ai.anomaly_detectors.protocol import Protocol, Scope, Window, series_key
    from retailops_ai.qualified_anomalies.contract import Point

    scopes = {}
    for line in (feature_dir / "features.jsonl").read_bytes().splitlines():
        point = Point.model_validate_json(line)
        scope = Scope(**{name: getattr(point, name) for name in Scope.model_fields})
        scopes[series_key(scope)] = scope
    return Protocol(
        scopes=tuple(scopes[key] for key in sorted(scopes)),
        train=Window(start=date(2026, 7, 2), end=date(2026, 7, 23)),
        validation=Window(start=date(2026, 7, 24), end=date(2026, 7, 26)),
        test=Window(start=date(2026, 7, 29), end=date(2026, 9, 2)),
        training_cutoff=datetime(2026, 7, 25, 23, 59, 59, tzinfo=UTC),
        selection_cutoff=datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC),
    )


def reader(root: Path, feature_dir: Path) -> dict[str, Any]:
    original_import = builtins.__import__

    def isolated_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".", 1)[0] in {"numpy", "scipy", "sklearn"}:
            raise ImportError("training_library_excluded_from_portable_reader")
        return original_import(name, *args, **kwargs)

    builtins.__import__ = isolated_import

    class TrainingImportBlock(MetaPathFinder):
        def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
            if fullname.split(".", 1)[0] in {"numpy", "scipy", "sklearn"}:
                raise ModuleNotFoundError("training_library_excluded_from_portable_reader")
            return None

    sys.meta_path.insert(0, TrainingImportBlock())
    from retailops_ai.anomaly_detectors.codec import baseline_score, forest_scores
    from retailops_ai.anomaly_detectors.contract import ModelManifest, Prediction, RunManifest
    from retailops_ai.anomaly_detectors.protocol import point_key
    from retailops_ai.qualified_anomalies.contract import MAX_BYTES, ModelRow, Point
    from retailops_ai.source_snapshot.files import canonical_json, json_sha256
    from retailops_ai.source_snapshot.files import read_bytes as read_artifact

    model_raw = read_artifact(root, "model.json", 8 * 1024**2)
    model = ModelManifest.model_validate_json(model_raw)
    run_raw = read_artifact(root, "run_manifest.json", 1024**2)
    run = RunManifest.model_validate_json(run_raw)
    test_raw = read_artifact(root, "test_scores.jsonl", 32 * 1024**2)
    if (
        model_raw != canonical_json(model.model_dump(mode="json")) + b"\n"
        or model.detector_id
        != "anomaly-detector-sha256-" + json_sha256(model.descriptor.model_dump(mode="json"))
        or run.run_artifact_id
        != "anomaly-detector-run-sha256-" + json_sha256(run.descriptor.model_dump(mode="json"))
        or run.descriptor.detector_id != model.detector_id
        or run.descriptor.model_manifest_sha256 != hashlib.sha256(model_raw).hexdigest()
        or read_artifact(root, "manifest.sha256", 128)
        != (hashlib.sha256(run_raw).hexdigest() + "\n").encode()
        or run.descriptor.test_scores_sha256 != hashlib.sha256(test_raw).hexdigest()
    ):
        raise ValueError("portable_reader_seal_or_identity")
    points = {
        point_key(point): point
        for line in read_artifact(feature_dir, "features.jsonl", MAX_BYTES).splitlines()
        for point in [Point.model_validate_json(line)]
    }
    predictions = [Prediction.model_validate_json(line) for line in test_raw.splitlines()]
    verified = 0
    for group in model.descriptor.groups:
        for family in ("seasonal_residual", "isolation_forest"):
            selected = [
                p
                for p in predictions
                if p.status == "scored"
                and p.family == family
                and p.event_type == group.event_type
                and p.currency == group.currency
            ]
            rows = []
            for prediction in selected:
                point = points[point_key(prediction)]  # type: ignore[arg-type]
                if point.status != "ready_input":
                    raise ValueError("portable_reader_scored_unready_input")
                # Independent data-only projection; importing the full feature builder
                # would also load native snapshot/Arrow dependencies.
                values = {
                    name: getattr(point, name)
                    for name in (
                        "expected_units",
                        "residual_units",
                        "robust_scale_units",
                        "standardized_residual",
                    )
                }
                values.update(
                    observed_units=point.observation.observed_units,
                    planned_price=float(point.context.planned_price)
                    if point.context.planned_price is not None
                    else None,
                    promotion_offered=point.context.promotion_offered,
                    on_hand=point.context.on_hand,
                )
                rows.append(ModelRow.model_validate(values))
            if family == "isolation_forest":
                if group.pipeline is None and rows:
                    raise ValueError("portable_reader_unfitted_forest")
                scores = forest_scores(group.pipeline, rows) if group.pipeline else ()
            else:
                scores = tuple(baseline_score(row) for row in rows)
            if list(scores) != [p.score for p in selected]:
                raise ValueError("portable_reader_score_mismatch")
            verified += len(selected)
    imported = sorted(
        {
            name.split(".", 1)[0]
            for name in sys.modules
            if name.split(".", 1)[0] in {"numpy", "scipy", "sklearn"}
        }
    )
    if not verified or imported:
        raise ValueError(
            f"portable_reader_training_import_or_empty_result: {verified=}, {imported=}"
        )
    return {
        "status": "passed",
        "detector_id": model.detector_id,
        "verified_test_scores": verified,
        "training_libraries_available": False,
    }


def worker(fixtures: Path, workspace: Path, selected_case: str) -> dict[str, Any]:
    from retailops_ai.anomaly_detectors.store import build, runtime, verify
    from retailops_ai.curated.builder import build_curated
    from retailops_ai.full_raw_dq.store import build_replay
    from retailops_ai.qualified_anomalies.store import build as build_features
    from retailops_ai.source_snapshot.files import json_sha256
    from retailops_ai.source_snapshot.importer import import_snapshot

    if (
        importlib.util.find_spec("data") is not None
        or importlib.util.find_spec("services") is not None
    ):
        raise ValueError("producer_namespace_available_in_detector_process")
    started = time.monotonic()
    stages = []

    def mark(case: str, stage: str) -> None:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        measurement = {
            "case": case,
            "stage": stage,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "cpu_seconds": round(usage.ru_utime + usage.ru_stime, 3),
        }
        stages.append(measurement)
        print(json.dumps(measurement, sort_keys=True), file=sys.stderr, flush=True)

    extract(fixtures, "full-raw-dq-v2", workspace / "inputs/full")
    extract(fixtures, "day-coverage-v1", workspace / "inputs/coverage")
    before = hashes(workspace / "inputs")
    cases = []
    resources = []
    if selected_case not in {"demand", "physical"}:
        raise ValueError("native_detector_unknown_public_profile")
    for case in (selected_case,):
        parent_root = workspace / case / "parents/data/generated"
        source = import_snapshot(
            workspace / "inputs/full" / case / "public",
            parent_root,
            required_use_cases=("anomaly_source",),
        )
        mark(case, "snapshot")
        curated = build_curated(source.directory, parent_root)
        mark(case, "curated")
        replay = build_replay(
            workspace / "inputs/full" / case / "capture",
            curated.directory,
            source.directory,
            parent_root,
        )
        mark(case, "full_dq")
        parents = (
            Path(replay["directory"]),
            workspace / "inputs/coverage" / case,
            curated.directory,
            source.directory,
        )
        features = build_features(*parents, parent_root)
        mark(case, "qualified_features")
        feature_dir = Path(features["directory"])
        protocol = development_protocol(feature_dir)
        before_parents = hashes(parent_root)
        result = build(
            feature_dir, *parents, workspace / case / "consumer/data/generated", protocol
        )
        mark(case, "detector_build")
        directory = Path(result["directory"])
        artifact_before = hashes(directory)
        manifest = verify(directory, feature_dir, *parents)
        mark(case, "independent_verification")
        portable = subprocess.run(  # noqa: S603 - fixed interpreter/script
            [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--reader",
                str(directory),
                "--feature-dir",
                str(feature_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )  # noqa: S603 - fixed interpreter/script
        if portable.returncode:
            raise RuntimeError("portable reader failed: " + portable.stderr[-8192:])
        portable_report = json.loads(portable.stdout)
        mark(case, "portable_reader")
        if (
            result["run_artifact_id"] != manifest.run_artifact_id
            or artifact_before != hashes(directory)
            or before_parents != hashes(parent_root)
            or portable_report["detector_id"] != result["detector_id"]
            or any(
                not result["prediction_status_counts"].get(f"{family}/{status}")
                for family in ("seasonal_residual", "isolation_forest")
                for status in ("scored", "insufficient_data")
            )
        ):
            raise ValueError("native_detector_acceptance_or_parent_mutation")
        cases.append(
            {
                "case": case,
                "detector_runtime_sha256": json_sha256(runtime().model_dump(mode="json")),
                "qualified_anomaly_input_id": features["qualified_anomaly_input_id"],
                "run_artifact_id": result["run_artifact_id"],
                "detector_id": result["detector_id"],
                "requested_rows": manifest.descriptor.requested_rows,
                "role_status_counts": result["role_status_counts"],
                "prediction_status_counts": result["prediction_status_counts"],
                "artifact_files": artifact_before,
                "portable_reader": portable_report,
            }
        )
        resources.append({"case": case, "fits": result["fit_resources"]})
    elapsed = time.monotonic() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024**2 if sys.platform == "darwin" else 1024
    )
    if hashes(workspace / "inputs") != before:
        raise ValueError("native_detector_parent_mutation")
    if elapsed > 300 or rss > 1024:
        raise ValueError(f"native_detector_process_budget: {elapsed=:.3f}, {rss=:.3f}")
    return {
        "elapsed_seconds": round(elapsed, 3),
        "peak_rss_mib": round(rss, 3),
        "parent_bytes_unchanged": True,
        "producer_imports_available": False,
        "evaluation_truth_available": False,
        "model_quality": "not_evaluated",
        "cases": cases,
        "fit_resources": resources,
        "stage_timings": stages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--case", choices=("demand", "physical"))
    parser.add_argument("--reader", type=Path)
    parser.add_argument("--feature-dir", type=Path)
    parser.add_argument("--fixture-dir", type=Path, default=ROOT / "data/fixtures")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve().is_relative_to(args.fixture_dir.resolve()):
        raise ValueError("detector_receipt_cannot_overwrite_fixtures")
    if args.reader:
        if args.feature_dir is None:
            raise ValueError("portable_reader_requires_feature_parent")
        report = reader(args.reader.resolve(), args.feature_dir.resolve())
    elif args.worker:
        if args.case is None:
            raise ValueError("native_detector_worker_requires_one_public_profile")
        report = worker(args.fixture_dir.resolve(), args.worker.resolve(), args.case)
    else:
        runs = []
        for _ in range(2):
            processes = []
            for case in ("demand", "physical"):
                with tempfile.TemporaryDirectory(prefix="ai07-anomaly-detectors-") as tmp:
                    try:
                        result = subprocess.run(  # noqa: S603 - fixed interpreter/script
                            [
                                sys.executable,
                                "-I",
                                str(Path(__file__).resolve()),
                                "--worker",
                                tmp,
                                "--case",
                                case,
                                "--fixture-dir",
                                str(args.fixture_dir.resolve()),
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=360,
                        )  # noqa: S603 - fixed interpreter/script
                    except subprocess.TimeoutExpired as exc:
                        stderr = (
                            exc.stderr.decode(errors="replace")
                            if isinstance(exc.stderr, bytes)
                            else exc.stderr or ""
                        )
                        raise RuntimeError(
                            "native detector process timed out: " + stderr[-8192:]
                        ) from exc
                    if result.returncode:
                        raise RuntimeError(
                            "native detector worker failed: " + result.stderr[-8192:]
                        )
                    processes.append(json.loads(result.stdout))
            runs.append(
                {
                    "processes": processes,
                    "cases": [case for process in processes for case in process["cases"]],
                }
            )
        if runs[0]["cases"] != runs[1]["cases"]:
            raise ValueError("native_anomaly_detectors_not_reproducible")
        report = {
            "status": "passed",
            "limits": {"seconds_per_process": 300, "peak_rss_mib": 1024},
            "runs": runs,
        }
        if args.output:
            args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
