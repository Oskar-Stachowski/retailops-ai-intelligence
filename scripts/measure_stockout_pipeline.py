"""Bounded, isolated resource probe of the complete accepted stockout smoke path.

The producer runs in a separate process. The consumer cannot import it. Temporary
outputs are removed after recording bounded metrics; no final-test scoring occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psutil  # type: ignore[import-untyped]

PRODUCER_COMMIT = "08639e9188badb352ed64686a088fe237badad41"
CONSUMER_COMMIT = "4faaf4b6c1997fda3a609645595165643bf302a9"
FREE_BYTES = 50 * 1024**3
SCRATCH_BYTES = 512 * 1024**2
RSS_BYTES = 1024**3
PILOT_V16_RSS_BYTES = 1280 * 1024**2
REMOTE_FREE_BYTES = 6 * 1024**3
WALL_SECONDS = 600
SAMPLE_SECONDS = 0.2
EXPECTED_DEVELOPMENT = (
    "development-sha256-8016d7a453a5bb3d718ea594a9c007ebed19e748917b11cb0e777f5a0352219a"
)


def without(value: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k not in keys}


def smoke_parity(
    result: dict[str, Any],
    reference: dict[str, Any],
    label: dict[str, Any],
    old_label: dict[str, Any],
    temporal: dict[str, Any],
    old_temporal: dict[str, Any],
) -> dict[str, Any]:
    """Compare logical regeneration without rewriting any timestamps, seals or IDs."""
    from retailops_ai.source_snapshot.files import json_sha256

    parents = result["descriptor"]["parents"]
    old_parents = reference["descriptor"]["parents"]
    label_seal, old_label_seal = (
        label["descriptor"]["input_seal"],
        old_label["descriptor"]["input_seal"],
    )
    temporal_seal = temporal["descriptor"]["input_seal"]
    old_temporal_seal = old_temporal["descriptor"]["input_seal"]
    checks = dict(
        models_equal=result["pipelines"] == reference["pipelines"],
        results_equal=result["results"] == reference["results"],
        report_equal=result["report"] == reference["report"],
        descriptor_except_parents_equal=without(result["descriptor"], "parents")
        == without(reference["descriptor"], "parents"),
        unchanged_parent_ids_equal=without(parents, "label_bundle_id", "temporal_bundle_id")
        == without(old_parents, "label_bundle_id", "temporal_bundle_id"),
        label_semantics_equal=without(label["descriptor"], "input_seal")
        == without(old_label["descriptor"], "input_seal"),
        label_content_seal_equal=without(label_seal, "snapshot_manifest_sha256")
        == without(old_label_seal, "snapshot_manifest_sha256"),
        label_report_equal=label["report"] == old_label["report"],
        temporal_semantics_equal=without(temporal["descriptor"], "parents", "input_seal")
        == without(old_temporal["descriptor"], "parents", "input_seal"),
        temporal_indexed_content_seal_equal=without(temporal_seal, "parent_files_sha256")
        == without(old_temporal_seal, "parent_files_sha256"),
        temporal_report_equal=temporal["report"] == old_temporal["report"],
        temporal_parent_pin=temporal["descriptor"]["parents"]
        == {k: parents[k] for k in ("feature_bundle_id", "label_bundle_id", "upstream_bundle_id")},
        actual_new_parents_bound=parents["label_bundle_id"] == label["label_bundle_id"]
        and parents["temporal_bundle_id"] == temporal["temporal_bundle_id"],
        reference_parents_bound=old_parents["label_bundle_id"] == old_label["label_bundle_id"]
        and old_parents["temporal_bundle_id"] == old_temporal["temporal_bundle_id"],
        identities_valid=all(
            doc[key] == prefix + json_sha256(doc["descriptor"])
            for doc, key, prefix in (
                (label, "label_bundle_id", "label-partitions-sha256-"),
                (temporal, "temporal_bundle_id", "temporal-partitions-sha256-"),
                (result, "development_id", "development-sha256-"),
            )
        ),
    )
    return dict(
        semantic_equal=all(checks.values()),
        checks=checks,
        development_id_equal=result["development_id"] == reference["development_id"],
        actual_parent_ids=parents,
        new_private_manifest_sha256=label_seal["snapshot_manifest_sha256"],
        reference_private_manifest_sha256=old_label_seal["snapshot_manifest_sha256"],
        scope="fully verified regenerated inputs; new raw manifest and parent seals preserved; no source checksum/timestamp/parent ID rewritten",
    )


def resource_gates(receipt: dict[str, Any]) -> bool:
    m, b = receipt["measurement"], receipt["budgets"]
    return bool(
        m["wall_seconds"] <= b["wall_seconds"]
        and m["sampled_tree_peak_rss_bytes"] <= b["tree_rss_bytes"]
        and max(m["sampled_peak_scratch_logical_bytes"], m["sampled_peak_scratch_allocated_bytes"])
        <= b["scratch_bytes"]
        and m["minimum_free_bytes"] >= b["minimum_free_bytes"]
    )


def pilot_preflight(profile: dict[str, Any], baseline: dict[str, Any], free: int) -> dict[str, Any]:
    """20% estimate headroom permits only an attempt; live limits remain mandatory."""
    consumer = baseline.get("consumer") or {}
    parity = consumer.get("parity") or {}
    measured_pilot = (
        baseline.get("whole_pilot_budget_passed") is True
        and baseline.get("larger_profile_qualified") is True
        and baseline.get("producer_commit") == PRODUCER_COMMIT
        and baseline.get("consumer_commit") == CONSUMER_COMMIT
        and baseline.get("final_test_outcomes_evaluated") is False
        and baseline.get("model_promoted") is False
        and consumer.get("final_test_outcomes_evaluated") is False
        and baseline.get("phases") is not None
        and [(p.get("phase"), p.get("exit_code")) for p in baseline["phases"]]
        == [("producer", 0), ("consumer", 0)]
    )
    if (
        baseline.get("status") != "passed"
        or not (parity.get("semantic_equal") is True or measured_pilot)
        or not resource_gates(baseline)
    ):
        raise ValueError("pilot_requires_qualified_smoke_resource_baseline")
    g, old = profile["generation"], baseline["producer"]["effective_config"]
    for key in ("days", "products", "stores", "warehouses", "seed", "max_daily_rows"):
        if type(g[key]) is not int or g[key] <= 0:
            raise ValueError("pilot_configuration_invalid")
    if (
        g["profile"] != "ai-load"
        or g["days"]
        != (date.fromisoformat(g["end_date"]) - date.fromisoformat(g["start_date"])).days + 1
    ):
        raise ValueError("pilot_configuration_invalid")
    physical = g["days"] * g["products"] * g["warehouses"]
    if (
        physical != profile["physical_origin_upper_bound"]
        or g["days"] * g["products"] * g["stores"] > g["max_daily_rows"]
    ):
        raise ValueError("pilot_configuration_grid_mismatch")
    ratio = max(
        physical / (old["days"] * old["products"] * old["warehouses"]),
        g["days"] * g["products"] * g["stores"] / (old["days"] * old["products"] * old["stores"]),
    )
    scale = ratio * 1.2
    m, s, b, caps = (
        baseline["measurement"],
        baseline["consumer"]["input_stats"],
        profile["budgets"],
        profile["unchanged_consumer_limits"],
    )
    # Old profiles retain their original 1GiB bound. A prospective1.6 profile
    # explicitly versions the resource-only increase after a measured exporter
    # exceeded that bound; quality requirements and protected roles do not change.
    remote = profile.get("schema_version") == "stockout-resource-pilot-1.7.0"
    if remote and (
        profile.get("execution_scope") != "isolated_github_hosted_runner"
        or sys.platform != "linux"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("GITHUB_REPOSITORY") != "Oskar-Stachowski/retailops-ai-intelligence"
    ):
        raise ValueError("pilot_remote_profile_requires_isolated_github_runner")
    required_free = REMOTE_FREE_BYTES if remote else FREE_BYTES
    rss_cap = (
        PILOT_V16_RSS_BYTES
        if profile.get("schema_version")
        in {"stockout-resource-pilot-1.6.0", "stockout-resource-pilot-1.7.0"}
        else RSS_BYTES
    )
    if (
        b["minimum_free_bytes"] < required_free
        or b["tree_rss_bytes"] > rss_cap
        or any(
            type(b[k]) is not int or b[k] <= 0
            for k in ("minimum_free_bytes", "tree_rss_bytes", "scratch_bytes", "wall_seconds")
        )
    ):
        raise ValueError("pilot_user_reserve_or_memory_limit_changed")
    estimates = dict(
        tree_rss_bytes=m["sampled_tree_peak_rss_bytes"] * scale,
        wall_seconds=m["wall_seconds"] * scale,
        scratch_bytes=max(
            m["sampled_peak_scratch_logical_bytes"], m["sampled_peak_scratch_allocated_bytes"]
        )
        * scale,
        input_rows=s["private"]["rows"] * scale,
        input_bytes=s["private"]["bytes"] * scale,
        ledger_rows=s["private"]["ledger_rows"] * scale,
        qualification_bytes=s["qualification_bytes"] * scale,
    )
    checks = {k: estimates[k] <= b[k] for k in ("tree_rss_bytes", "wall_seconds", "scratch_bytes")}
    checks.update(
        {
            k: estimates[k] <= caps[k]
            for k in ("input_rows", "input_bytes", "ledger_rows", "qualification_bytes")
        }
    )
    checks.update(
        free_disk=free >= b["minimum_free_bytes"] + b["scratch_bytes"],
        physical_origins=physical <= caps["physical_origins"],
    )
    return dict(
        ready_to_attempt=all(checks.values()),
        estimates=estimates,
        checks=checks,
        scale_from_measured_smoke=ratio,
        estimate_headroom_fraction=0.2,
        measured_baseline="qualified_intermediate_pilot" if measured_pilot else "qualified_smoke",
        scope="heuristic estimates, not guaranteed upper bounds; databases, selected arrays and all input caps still enforced by actual pipeline",
        larger_profile_qualified=False,
        quality_qualified=False,
    )


@contextmanager
def owned_scratch(parent: Path, retain: bool) -> Iterator[tuple[Path, dict[str, bool]]]:
    root = Path(tempfile.mkdtemp(prefix="ai08-whole-probe-", dir=parent))
    state = {"success": False}
    try:
        yield root, state
    finally:
        if not retain or not state["success"]:
            shutil.rmtree(root)


def write_json(path: Path, value: Any) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    path.chmod(0o600)


def tree_size(root: Path) -> dict[str, int]:
    logical = allocated = count = 0

    def disappeared(error: OSError) -> None:
        if not isinstance(error, FileNotFoundError):
            raise error

    for directory, _, names in os.walk(root, onerror=disappeared, followlinks=False):
        for name in names:
            try:
                info = os.stat(os.path.join(directory, name), follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                logical += info.st_size
                allocated += info.st_blocks * 512
                count += 1
    return dict(logical_bytes=logical, allocated_bytes=allocated, files=count)


def stage(name: str, action: Any, records: list[dict[str, Any]]) -> Any:
    print(json.dumps(dict(event="started", stage=name)), flush=True)
    wall, cpu = time.perf_counter(), time.process_time()
    result = action()
    record = dict(
        stage=name, wall_seconds=time.perf_counter() - wall, cpu_seconds=time.process_time() - cpu
    )
    records.append(record)
    print(json.dumps(record), flush=True)
    return result


def produce(root: Path) -> None:
    # The producer uses a separate pinned checkout only in this child.
    # The exporter requires data/generated inside its own repository. Use an
    # isolated local clone; neither the frozen producer nor its inputs are changed.
    records: list[dict[str, Any]] = []
    git = shutil.which("git")
    if git is None:
        raise ValueError("resource_probe_git_unavailable")
    code = root / "producer-code"

    def checkout() -> None:
        subprocess.check_call(  # noqa: S603 - local pinned repository, no shell
            [git, "clone", "--shared", "--quiet", os.environ["RETAILOPS_PROBE_PRODUCER"], str(code)]
        )
        subprocess.check_call([git, "checkout", "--quiet", "--detach", PRODUCER_COMMIT], cwd=code)  # noqa: S603

    stage("producer_checkout", checkout, records)
    sys.path.insert(0, str(code))
    importlib.invalidate_caches()
    configuration = importlib.import_module("data.generator.configuration")
    source_api = importlib.import_module("data.inventory.run_source_dataset")
    qualification_api = importlib.import_module("data.inventory.run_qualification")
    export_api = importlib.import_module("data.export.inventory_snapshot")
    generated = code / "data/generated/resource-probe"
    profile = (
        json.loads((root / "pilot-config.json").read_text())
        if (root / "pilot-config.json").is_file()
        else None
    )
    generation = (
        dict(profile["generation"]) if profile else dict(profile="ai-temporal-smoke", seed=42)
    )
    for key in ("start_date", "end_date"):
        if key in generation:
            generation[key] = date.fromisoformat(str(generation[key]))
    config = configuration.DatasetGenerationConfig(**generation)
    resolved = configuration.resolve_generation_config(config)
    source = stage("source", lambda: source_api.run(config, generated / "source"), records)
    qualification = stage(
        "qualification",
        lambda: qualification_api.run(Path(source["directory"]), generated / "qualification"),
        records,
    )
    paths = dict(source=source["directory"], qualification=qualification["directory"])
    for name, private in (("facts", False), ("private", True)):
        export = stage(
            name + "_export",
            lambda private=private, name=name: export_api.export_inventory_snapshot(
                Path(source["directory"]),
                source["dataset_id"],
                Path(qualification["directory"]),
                generated / name,
                include_truth=private,
            ),
            records,
        )
        paths[name + "_export"] = export["path"]
    write_json(
        root / "producer.json",
        dict(
            paths=paths,
            stages=records,
            effective_config=resolved.parameters(),
            source_dataset_id=source["dataset_id"],
            qualification_id=qualification["qualification_id"],
        ),
    )


def consume(root: Path) -> None:
    from retailops_ai.curated.builder import build_curated
    from retailops_ai.source_snapshot.files import canonical_json
    from retailops_ai.source_snapshot.importer import import_snapshot
    from retailops_ai.stockout.split import SplitPolicy
    from retailops_ai.stockout_history.bundle import build_history_bundle
    from retailops_ai.stockout_label_partitions.bundle import build_label_bundle
    from retailops_ai.stockout_temporal_series.bundle import (
        assemble_partitioned_development,
        build_temporal_bundle,
    )
    from retailops_ai.stockout_temporal_storage.store import PartitionInputs
    from retailops_ai.stockout_training.development import build_development
    from retailops_ai.stockout_upstream_series.bundle import build_upstream_bundle

    for name in ("data", "ml", "retailops"):
        if importlib.util.find_spec(name) is not None:
            raise ValueError("producer_importable_in_consumer:" + name)
    paths = json.loads((root / "producer.json").read_text())["paths"]
    profile = (
        json.loads((root / "pilot-config.json").read_text())
        if (root / "pilot-config.json").is_file()
        else None
    )
    records: list[dict[str, Any]] = []
    input_stats: dict[str, Any] = {}
    for name, private in (("facts", False), ("private", True)):
        imported = stage(
            name + "_import",
            lambda private=private, name=name: import_snapshot(
                Path(paths[name + "_export"]),
                root / (name + "_import") / "data/generated",
                allow_evaluation_truth=private,
                required_use_cases=("forecast_source", "inventory_source"),
            ),
            records,
        )
        paths[name + "_import"] = str(imported.directory)
        input_stats[name] = dict(
            snapshot_id=imported.snapshot.snapshot_id,
            rows=sum(t["row_count"] for t in imported.snapshot.manifest["tables"]),
            bytes=sum(r["bytes"] for r in imported.snapshot.references),
            ledger_rows=sum(
                t["row_count"]
                for t in imported.snapshot.manifest["tables"]
                if t["table"] == "inventory_ledger"
            ),
        )
        if not private:
            curated = stage(
                "curated",
                lambda imported=imported: build_curated(
                    imported.directory, root / "curated/data/generated"
                ),
                records,
            )
            paths["curated"] = str(curated.directory)
            print(
                json.dumps(dict(curated_dataset_id=curated.manifest["curated_dataset_id"])),
                flush=True,
            )
    curated_root = Path(paths["curated"])
    private_root = Path(paths["private_import"]) / "snapshot"
    input_stats["qualification_bytes"] = (
        (
            private_root
            / "evaluation_truth/qualification/simulation_truth/inventory_qualified_windows.json"
        )
        .stat()
        .st_size
    )
    features, upstream, labels, temporal = (
        root / n for n in ("features", "upstream", "labels", "temporal")
    )
    feature_doc, _ = stage(
        "features", lambda: build_history_bundle(curated_root, features), records
    )
    label_doc, _ = stage(
        "labels",
        lambda: build_label_bundle(private_root, labels, allow_evaluation_truth=True),
        records,
    )
    upstream_doc, _ = stage(
        "upstream", lambda: build_upstream_bundle(curated_root, features, upstream), records
    )
    inputs = PartitionInputs(curated_root, private_root, features, upstream, labels)
    policy = SplitPolicy.model_validate(
        dict(
            start_at="2026-04-21T00:00:00Z",
            train_until="2026-06-05T00:00:00Z",
            tune_until="2026-06-24T00:00:00Z",
            calibration_until="2026-07-13T00:00:00Z",
            test_until="2026-08-01T00:00:00Z",
            evaluated_at="2026-08-01T00:00:00Z",
        )
    )
    if profile is not None:
        policy = SplitPolicy.model_validate(profile["split_policy"])
    temporal_doc, _ = stage(
        "temporal",
        lambda: build_temporal_bundle(
            inputs, temporal, split_policy=policy, allow_evaluation_truth=True
        ),
        records,
    )
    data = stage(
        "assemble_development",
        lambda: assemble_partitioned_development(temporal, inputs, allow_evaluation_truth=True),
        records,
    )
    result = stage("fit_six_models", lambda: build_development(data), records)
    write_json(root / "development.json", result)
    groups = {
        n: tree_size(Path(paths[n]) if n in paths else root / n)
        for n in (
            "source",
            "qualification",
            "facts_export",
            "private_export",
            "facts_import",
            "private_import",
            "curated",
            "features",
            "upstream",
            "labels",
            "temporal",
        )
    }
    parity = (
        None
        if profile is not None
        else smoke_parity(
            result,
            json.loads((root / "reference-development.json").read_text()),
            label_doc,
            json.loads((root / "reference-label.json").read_text()),
            temporal_doc,
            json.loads((root / "reference-temporal.json").read_text()),
        )
    )
    write_json(
        root / "development-inputs.json",
        dict(
            rows=data.rows,
            outcomes=data.outcomes,
            coverage=data.coverage,
            parents=data.parents,
            split_policy=data.split_policy.model_dump(mode="json"),
            categorical_lineage_sha256=data.categorical_lineage_sha256,
            final_test_outcomes_included=False,
        ),
    )
    write_json(
        root / "consumer.json",
        dict(
            stages=records,
            input_stats=input_stats,
            parity=parity,
            paths=paths,
            ids=dict(
                feature_bundle_id=feature_doc["feature_bundle_id"],
                label_bundle_id=label_doc["label_bundle_id"],
                upstream_bundle_id=upstream_doc["upstream_bundle_id"],
                temporal_bundle_id=temporal_doc["temporal_bundle_id"],
                development_id=result["development_id"],
            ),
            groups=groups,
            selected_on_tune=result["report"]["selected_on_tune"],
            development_rows_by_role={k: len(v) for k, v in data.rows.items()},
            development_payload_bytes=len(
                canonical_json(dict(rows=data.rows, outcomes=data.outcomes))
            ),
            physical_origins=temporal_doc["descriptor"]["rows"],
            final_test_membership=temporal_doc["report"]["split"]["eligible_by_role"]["test"],
            development_report=result["report"],
            final_test_outcomes_evaluated=False,
            model_promoted=False,
            producer_roots_unimportable=["data", "ml", "retailops"],
        ),
    )
    print(json.dumps(dict(parity=parity)), flush=True)
    if parity is not None and not parity["semantic_equal"]:
        raise ValueError("smoke_development_semantics_changed")


def stop_owned(child: subprocess.Popen[Any]) -> None:
    # Each child has a new session; this group never contains another user session.
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # A leader may exit while a descendant ignores SIGTERM. Reap the whole group.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=5)


def run_probe(
    producer: Path,
    receipt_path: Path,
    reference: Path | None = None,
    pilot: Path | None = None,
    baseline: Path | None = None,
) -> int:
    # Pin the actual producer and consumer revisions, independent of remote branches.
    consumer = Path(__file__).resolve().parent.parent
    git = shutil.which("git")
    if git is None:
        raise ValueError("resource_probe_git_unavailable")
    for root, expected in ((producer, PRODUCER_COMMIT),):
        actual = subprocess.check_output([git, "rev-parse", "HEAD"], cwd=root, text=True).strip()  # noqa: S603
        if actual != expected:
            raise ValueError("resource_probe_revision_mismatch")
        subprocess.check_call([git, "diff", "--exit-code", expected], cwd=root)  # noqa: S603
    subprocess.check_call(  # noqa: S603 - fixed read-only git diff
        [
            git,
            "diff",
            "--exit-code",
            CONSUMER_COMMIT,
            "--",
            "src",
            "contracts",
            "pyproject.toml",
            "uv.lock",
        ],
        cwd=consumer,
    )  # noqa: S603
    free_before = shutil.disk_usage(receipt_path.parent).free
    profile = json.loads(pilot.read_text()) if pilot is not None else None
    preflight = None
    limits = dict(
        scratch_bytes=SCRATCH_BYTES,
        tree_rss_bytes=RSS_BYTES,
        wall_seconds=WALL_SECONDS,
        minimum_free_bytes=FREE_BYTES,
    )
    if profile is not None:
        if baseline is None:
            raise ValueError("pilot_resource_baseline_required")
        if (
            profile["producer_commit"] != PRODUCER_COMMIT
            or profile["consumer_baseline_commit"] != CONSUMER_COMMIT
        ):
            raise ValueError("pilot_baseline_revision_mismatch")
        preflight = pilot_preflight(profile, json.loads(baseline.read_text()), free_before)
        if not preflight["ready_to_attempt"]:
            write_json(
                receipt_path,
                dict(
                    status="not_ready",
                    preflight=preflight,
                    larger_profile_generation_attempted=False,
                    larger_profile_qualified=False,
                    ai08_ready=False,
                ),
            )
            return 2
        limits = profile["budgets"]
    if receipt_path.exists():
        raise ValueError("resource_probe_receipt_already_exists")
    if free_before < limits["minimum_free_bytes"] + limits["scratch_bytes"]:
        raise ValueError("resource_probe_insufficient_free_space")
    started, cpu = time.perf_counter(), time.process_time()
    peak_rss = peak_logical = peak_allocated = peak_files = samples = 0
    minimum_free, failure = free_before, None
    phases: list[dict[str, Any]] = []
    self_process = psutil.Process()
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    retained_root = None
    with owned_scratch(receipt_path.parent, retain=profile is not None) as (root, owned):
        root.chmod(0o700)
        (root / "tmp").mkdir(mode=0o700)
        if reference is not None:
            original = json.loads(reference.read_text())
            if original["development_id"] != EXPECTED_DEVELOPMENT:
                raise ValueError("resource_probe_reference_mismatch")
            write_json(root / "reference-development.json", original)
            old_label = json.loads(
                (reference.parent / "label-partitions-native-clock/manifest.json").read_text()
            )
            old_temporal = json.loads(
                (reference.parent / "temporal-series-native-accepted/manifest.json").read_text()
            )
            if (
                old_label["label_bundle_id"] != original["descriptor"]["parents"]["label_bundle_id"]
                or old_temporal["temporal_bundle_id"]
                != original["descriptor"]["parents"]["temporal_bundle_id"]
            ):
                raise ValueError("resource_probe_reference_parent_mismatch")
            write_json(root / "reference-label.json", old_label)
            write_json(root / "reference-temporal.json", old_temporal)
        if profile is not None:
            write_json(root / "pilot-config.json", profile)
        for phase in ("producer", "consumer"):
            environment = dict(
                os.environ,
                TMPDIR=str(root / "tmp"),
                PYTHONDONTWRITEBYTECODE="1",
                PYTHONHASHSEED="0",
            )
            environment.pop("PYTHONPATH", None)
            if phase == "producer":
                environment["PYTHONPATH"] = str(root / "producer-code")
                environment["RETAILOPS_PROBE_PRODUCER"] = str(producer)
            log = root / (phase + ".log")
            with log.open("w") as output:
                child = subprocess.Popen(  # noqa: S603 - own fixed script, shell disabled
                    [
                        sys.executable,
                        "-P",
                        str(Path(__file__).resolve()),
                        "--phase",
                        phase,
                        "--scratch",
                        str(root),
                    ],
                    cwd=root,
                    env=environment,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )  # noqa: S603
                try:
                    while True:
                        rss = 0
                        for process in [self_process, *self_process.children(recursive=True)]:
                            try:
                                rss += process.memory_info().rss
                            except psutil.NoSuchProcess:
                                continue
                        size = tree_size(root)
                        free = shutil.disk_usage(root).free
                        peak_rss = max(peak_rss, rss)
                        peak_logical = max(peak_logical, size["logical_bytes"])
                        peak_allocated = max(peak_allocated, size["allocated_bytes"])
                        peak_files = max(peak_files, size["files"])
                        minimum_free = min(minimum_free, free)
                        samples += 1
                        if rss > limits["tree_rss_bytes"]:
                            failure = "whole_pipeline_rss_budget_exceeded"
                        elif (
                            max(size["logical_bytes"], size["allocated_bytes"])
                            > limits["scratch_bytes"]
                        ):
                            failure = "whole_pipeline_scratch_budget_exceeded"
                        elif free < limits["minimum_free_bytes"]:
                            failure = "whole_pipeline_free_disk_reserve_exceeded"
                        elif time.perf_counter() - started > limits["wall_seconds"]:
                            failure = "whole_pipeline_wall_budget_exceeded"
                        if failure:
                            stop_owned(child)
                            break
                        if child.poll() is not None:
                            break
                        time.sleep(SAMPLE_SECONDS)
                    child.wait()
                finally:
                    if child.poll() is None:
                        stop_owned(child)
            log_bytes = log.read_bytes()
            phases.append(
                dict(
                    phase=phase,
                    exit_code=child.returncode,
                    log_sha256=hashlib.sha256(log_bytes).hexdigest(),
                    log_tail=log_bytes[-4000:].decode(errors="replace"),
                )
            )
            print(
                json.dumps(
                    dict(
                        phase=phase,
                        exit_code=child.returncode,
                        elapsed_seconds=time.perf_counter() - started,
                    )
                ),
                flush=True,
            )
            if failure or child.returncode:
                failure = failure or (phase + "_failed")
                break
        producer_result = (
            json.loads((root / "producer.json").read_text())
            if (root / "producer.json").is_file()
            else None
        )
        consumer_result = (
            json.loads((root / "consumer.json").read_text())
            if (root / "consumer.json").is_file()
            else None
        )
        if failure is None and (producer_result is None or consumer_result is None):
            failure = "whole_pipeline_receipt_missing"
        elapsed = time.perf_counter() - started
        final_size = tree_size(root)
        owned["success"] = failure is None
        if profile is not None and failure is None:
            retained_root = str(root)
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    receipt = dict(
        schema_version="stockout-whole-resource-probe-1.1.0"
        if profile
        and profile.get("schema_version")
        in {"stockout-resource-pilot-1.6.0", "stockout-resource-pilot-1.7.0"}
        else "stockout-whole-resource-probe-1.0.0",
        recorded_at_utc=datetime.now(UTC).isoformat(),
        status="passed" if failure is None else "failed",
        failure=failure,
        scope="one fresh complete "
        + ("intermediate ai-load pilot" if profile is not None else "ai-temporal-smoke")
        + " pipeline; producer, exports, import, curated, actual partition parents, assembly and six fits; sampled whole process tree",
        producer_commit=PRODUCER_COMMIT,
        consumer_commit=CONSUMER_COMMIT,
        budgets=limits,
        measurement=dict(
            wall_seconds=elapsed,
            parent_cpu_seconds=time.process_time() - cpu,
            child_cpu_seconds=(
                usage_after.ru_utime
                + usage_after.ru_stime
                - usage_before.ru_utime
                - usage_before.ru_stime
            ),
            sampled_tree_peak_rss_bytes=peak_rss,
            sampled_peak_scratch_logical_bytes=peak_logical,
            sampled_peak_scratch_allocated_bytes=peak_allocated,
            sampled_peak_files=peak_files,
            sample_seconds=SAMPLE_SECONDS,
            samples=samples,
            final_scratch=final_size,
            free_before_bytes=free_before,
            minimum_free_bytes=minimum_free,
            free_after_cleanup_bytes=shutil.disk_usage(receipt_path.parent).free,
        ),
        producer=producer_result,
        consumer=consumer_result,
        phases=phases,
        profile=profile,
        preflight=preflight,
        retained_root=retained_root,
        temporary_outputs_removed=retained_root is None,
        whole_smoke_budget_passed=failure is None and profile is None,
        whole_pilot_budget_passed=failure is None and profile is not None,
        larger_profile_generation_attempted=profile is not None,
        larger_profile_generated=profile is not None and producer_result is not None,
        larger_profile_qualified=failure is None and profile is not None,
        full_ai_dev_or_ai_training_qualified=False,
        independent_quality_accepted=False,
        final_test_outcomes_evaluated=False,
        model_promoted=False,
        ai08_ready=False,
    )
    write_json(receipt_path, receipt)
    print(json.dumps({k: receipt[k] for k in ("status", "failure", "measurement")}), flush=True)
    return 0 if failure is None else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--reference-development", type=Path)
    parser.add_argument("--pilot-config", type=Path)
    parser.add_argument("--baseline-resource", type=Path)
    parser.add_argument("--phase", choices=("producer", "consumer"))
    parser.add_argument("--scratch", type=Path)
    args = parser.parse_args()
    if args.phase:
        if args.scratch is None:
            parser.error("phase requires scratch")
        if args.phase == "producer":
            produce(args.scratch)
        else:
            consume(args.scratch)
        return 0
    if (
        args.producer is None
        or args.receipt is None
        or (args.reference_development is None and args.pilot_config is None)
    ):
        parser.error("probe requires producer, receipt and reference-development or pilot-config")
    return run_probe(
        args.producer.resolve(),
        args.receipt.absolute(),
        args.reference_development,
        args.pilot_config,
        args.baseline_resource,
    )


if __name__ == "__main__":
    raise SystemExit(main())
