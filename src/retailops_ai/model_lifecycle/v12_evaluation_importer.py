"""Project a verified, completed export; scope includes every retained, excluded membership."""

import gzip
import hashlib
import json
from collections import Counter

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.model_lifecycle.v12_evidence import V12Evidence
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    DEVELOPMENT_MODEL,
    MODEL,
    TEST_MODEL,
)
from retailops_ai.model_lifecycle.v12_metadata_contracts import (
    V12EvaluationDescriptor,
    V12EvaluationEvidence,
    evaluation_identity,
)
from retailops_ai.source_snapshot.files import decode_json, read_json, regular_file

MAX_MEMBERSHIPS = 50_000_000
MAX_LINE_BYTES = 16 * 1024


def project_evaluation(evidence: V12Evidence, *, model: str = MODEL) -> V12EvaluationEvidence:
    if model not in {MODEL, TEST_MODEL, DEVELOPMENT_MODEL}:
        raise ValueError("v12_evaluation_namespace")
    evidence.verify_bytes()
    metrics = evidence.metrics
    campaign = read_json(evidence.root, "campaign/campaign_manifest.json")
    records = campaign["descriptor"]["cohorts"]
    lineage = sorted(
        (
            dict(
                cohort_id=r["descriptor"]["cohort_id"],
                prepared_cohort_id=r["cohort_artifact_id"],
                source_dataset_id=r["descriptor"]["source_dataset_id"],
                snapshot_id=r["descriptor"]["snapshot_id"],
            )
            for r in records
        ),
        key=lambda r: r["cohort_id"],
    )
    cohorts = [r["cohort_id"] for r in lineage]
    expected_files = {f"campaign/predictions/{c}.jsonl.gz" for c in cohorts}
    if (
        not 1 <= len(cohorts) <= 64
        or len(set(cohorts)) != len(cohorts)
        or sorted(metrics["cohorts"]) != cohorts
        or expected_files != {f for f in evidence.files if f.startswith("campaign/predictions/")}
    ):
        raise ValueError("v12_evaluation_membership_inventory")
    products, locations, channels = set(), set(), set()
    counts: Counter[tuple[str, str]] = Counter()
    eligible: Counter[tuple[str, str]] = Counter()
    digest = hashlib.sha256()
    rows = 0
    for cohort in cohorts:
        with regular_file(evidence.root, f"campaign/predictions/{cohort}.jsonl.gz") as raw:
            with gzip.GzipFile(fileobj=raw) as stream:
                while line := stream.readline(MAX_LINE_BYTES + 1):
                    if len(line) > MAX_LINE_BYTES or rows >= MAX_MEMBERSHIPS:
                        raise ValueError("v12_evaluation_membership_budget")
                    row = decode_json(line)
                    key = json.loads(row["key"])
                    if (
                        not isinstance(key, list)
                        or len(key) != 7
                        or row["cohort_id"] != cohort
                        or row["fold"] != key[0]
                        or row["role"] != key[1]
                        or row["channel"] != key[5]
                        or row["role"] not in {"validation", "development_holdout"}
                        or row["fold"] not in metrics["required_folds"]
                        or row["fold"] == "pooled"
                        or row["observation"]["key"] != row["key"]
                    ):
                        raise ValueError("v12_evaluation_membership_key")
                    entity = ForecastKey.model_validate_json(
                        json.dumps(
                            dict(
                                product_id=key[3],
                                selling_location_id=key[4],
                                channel=key[5],
                                forecast_origin=key[2],
                                target_date=key[6],
                                horizon_days=row["horizon"],
                                business_timezone="UTC",
                                cutoff_policy="end_of_day_second_v1",
                            )
                        )
                    )
                    products.add(entity.product_id)
                    locations.add(entity.selling_location_id)
                    channels.add(entity.channel)
                    if len(products) > 10000 or len(locations) > 1000:
                        raise ValueError("v12_evaluation_scope_budget")
                    membership = (row["fold"], row["role"])
                    excluded = bool(row["observation"]["exclusion_reasons"])
                    counts[membership] += 1
                    eligible[membership] += not excluded
                    digest.update(canonical_bytes([cohort, *key, excluded]) + b"\n")
                    rows += 1
    global_segments = [
        s for s in metrics["segments"] if s["dimension"] == "global" and s["fold"] != "pooled"
    ]
    if (
        rows == 0
        or rows != metrics["processed_rows"]
        or counts != Counter({(s["fold"], s["role"]): s["total_rows"] for s in global_segments})
        or eligible
        != Counter({(s["fold"], s["role"]): s["eligible_rows"] for s in global_segments})
    ):
        raise ValueError("v12_evaluation_scope_row_coverage")
    descriptor = evidence.manifest["descriptor"]
    content = dict(
        version="forecast-v12-evaluation-evidence-1.0.0",
        evaluation_id=evaluation_identity(model, evidence.run_id),
        descriptor=dict(
            model_name=model,
            original_export_run_id=evidence.run_id,
            campaign_id=descriptor["campaign_id"],
            freeze_id=descriptor["freeze_id"],
            replay_id=descriptor["replay_id"],
            exported_at=evidence.manifest["exported_at"],
            scope=dict(
                product_ids=sorted(products),
                selling_location_ids=sorted(locations),
                channels=sorted(channels),
            ),
            cohort_lineage=lineage,
            membership_rows=rows,
            membership_sha256=digest.hexdigest(),
            source_code_commit=evidence.freeze["descriptor"]["remote_preparation"]["source_commit"],
            ai_code_commit=descriptor["ai_code_commit"],
            code_sha256=descriptor["code"]["code_sha256"],
            dependency_lock_sha256=descriptor["code"]["dependency_lock_sha256"],
            export_manifest=evidence.manifest_receipt.model_dump(mode="json"),
            metrics_report=evidence.files["campaign/metrics.json"].model_dump(mode="json"),
            quality_status=metrics["status"],
            forecast_model_status=descriptor["forecast_model_status"],
            input_complete=metrics["input_complete"],
            execution_failure_recorded=metrics["execution_failure"] is not None,
            segment_counts=metrics["segment_counts"],
            failed_reasons=metrics["failed_reasons"],
            registered_model_version=None,
            serving_eligible=False,
            portfolio_final_test="not_included_not_opened",
            metric_verification="pinned_original_export_and_replay_verified",
            quality_verification="recorded_status_not_requalified",
        ),
        original_metrics=metrics,
    )
    content["descriptor"] = V12EvaluationDescriptor.model_validate_json(
        json.dumps(content["descriptor"])
    ).model_dump(mode="json")
    content["evidence_sha256"] = canonical_sha256(content)
    result = V12EvaluationEvidence.model_validate_json(json.dumps(content))
    evidence.verify_bytes()
    return result
