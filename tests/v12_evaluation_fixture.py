"""Invented two-product transport fixture; original verifier is explicitly doubled."""

import gzip
import json
from copy import deepcopy
from pathlib import Path

from test_v12_evidence import verifier_receipt, write_fixture

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.model_lifecycle import v12_evidence
from retailops_ai.model_lifecycle.v12_evaluation_importer import project_evaluation
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import TEST_MODEL
from retailops_ai.source_snapshot.files import file_hash


def evaluation_fixture(root, monkeypatch, *, passed=False, model=TEST_MODEL):
    write_fixture(root, "passed" if passed else "not_ready")
    point = dict(
        complete=True,
        mae=1.25,
        mse=2.5,
        bias_units=0.0,
        normalized_bias=None,
        wape=None,
        zero_actual_excess_units=0.0,
    )
    functional = dict(
        rows=1,
        actual_sum=0,
        median=point,
        mean=point | dict(mae=3.5, mse=14.0),
        interval=dict(
            complete=True, mean_width=4.0, width_to_mean_actual=None, mean_score=8.0, coverage=0.9
        ),
    )
    segment = dict(
        fold="fold-1",
        role="development_holdout",
        dimension="global",
        value="all",
        protocol_version="forecast-quality-2.0.0",
        scope="segment_component_not_campaign_qualification",
        retained_median_baseline=True,
        eligible_rows=1,
        total_rows=2,
        eligibility_coverage=0.5,
        candidate=deepcopy(functional),
        baseline=deepcopy(functional),
        relative_median_mae_change=0.0,
        perfect_median_baseline_tied=False,
        diagnostics=dict(
            mean_mae_above_legacy_regression_limit=False, median_bias_above_legacy_mean_limit=None
        ),
        legacy_width_ratio_exceeded=None,
        status="passed" if passed else "failed",
        has_measurable_failures=not passed,
        not_ready_reasons=[],
        failed_reasons=[] if passed else ["mean_mse_regression"],
        calibration_evidence={
            s: dict(minimum_rows=20, missing_evidence_rows=0) for s in ("candidate", "baseline")
        },
    )
    pooled = deepcopy(segment) | dict(fold="pooled")
    if not passed:
        pooled.update(
            status="not_ready",
            has_measurable_failures=False,
            not_ready_reasons=["insufficient_sample"],
            failed_reasons=[],
        )
    metrics = dict(
        status="passed" if passed else "not_ready",
        input_complete=True,
        execution_failure=None,
        processed_rows=2,
        segment_counts={"passed": 2} if passed else {"failed": 1, "not_ready": 1},
        failed_reasons={} if passed else {"mean_mse_regression": 1},
        segments=[segment, pooled],
        required_folds=["fold-1", "pooled"],
        required_roles=["development_holdout"],
        cohorts=["seed-1"],
        portfolio_final_test="not_included_not_opened",
    )
    campaign = json.loads((root / "campaign/campaign_manifest.json").read_bytes())
    campaign["descriptor"] = dict(
        cohorts=[
            dict(
                cohort_artifact_id="forecast-cohort-sha256-" + "1" * 64,
                descriptor=dict(
                    cohort_id="seed-1",
                    source_dataset_id="source-sha256-" + "2" * 64,
                    snapshot_id="snapshot-sha256-" + "3" * 64,
                ),
            )
        ]
    )
    for name, body in (
        ("campaign/metrics.json", metrics),
        ("campaign/campaign_manifest.json", campaign),
    ):
        (root / name).write_bytes(canonical_bytes(body) + b"\n")
    rows = []
    for index, product in enumerate(("p-101", "p-202")):
        key = canonical_bytes(
            [
                "fold-1",
                "development_holdout",
                "2026-09-01T23:59:59+00:00",
                product,
                "s-03",
                "store",
                "2026-09-02",
            ]
        ).decode()
        rows.append(
            dict(
                cohort_id="seed-1",
                fold="fold-1",
                role="development_holdout",
                key=key,
                horizon=1,
                channel="store",
                observation=dict(key=key, exclusion_reasons=["stockout"] if index else []),
            )
        )
    path = root / "campaign/predictions/seed-1.jsonl.gz"
    path.parent.mkdir()
    path.write_bytes(gzip.compress(b"".join(canonical_bytes(row) + b"\n" for row in rows), mtime=0))
    manifest = json.loads((root / "run_manifest.json").read_bytes())
    manifest["descriptor"]["files"]["campaign/predictions/seed-1.jsonl.gz"] = {}
    for name in manifest["descriptor"]["files"]:
        size, sha = file_hash(root, name)
        manifest["descriptor"]["files"][name] = dict(size_bytes=size, sha256=sha)
    manifest["descriptor"]["bytes"] = sum(
        r["size_bytes"] for r in manifest["descriptor"]["files"].values()
    )
    manifest["run_id"] = "functional-v12-run-sha256-" + canonical_sha256(manifest["descriptor"])
    (root / "run_manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
    monkeypatch.setattr(v12_evidence, "verify_with_wheel", lambda root, *_: verifier_receipt(root))
    evidence = v12_evidence.load_evidence(root, Path("/explicit-test-verifier/python"))
    return evidence, project_evaluation(evidence, model=model)
