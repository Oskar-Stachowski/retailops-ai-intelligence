"""Replay temporal membership before exporting development outcomes only."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from retailops_ai.stockout.split import ROLES, SplitPolicy, development_labels, key
from retailops_ai.stockout.upstream_dataset import build_comparison, digest
from retailops_ai.stockout_training.contract import MAX_ROWS


@dataclass(frozen=True)
class DevelopmentData:
    rows: dict[str, list[dict[str, Any]]]
    outcomes: dict[str, list[int]]
    parents: dict[str, str]
    split_policy: SplitPolicy
    coverage: dict[str, Any]
    categorical_lineage_sha256: str


def category_at(
    catalog: list[dict[str, Any]], product: str, origin: datetime
) -> tuple[str, str | None]:
    known = [
        r
        for r in catalog
        if r["id"] == product
        and r["curated_available_at"] is not None
        and r["curated_available_at"] <= origin
    ]
    if len(known) > 1:
        raise ValueError("stockout_training_ambiguous_PIT_product_category")
    if not known:
        return "__unknown__", None
    return known[0]["category_id"], known[0]["source_record_sha256"]


def assemble_development(
    features: dict[str, Any],
    upstream: dict[str, Any],
    comparison: dict[str, Any],
    labels: dict[str, Any],
    split: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> DevelopmentData:
    """Parents must already be fully verified; never materialize a test outcome vector."""
    if comparison != build_comparison(features, upstream):
        raise ValueError("stockout_training_comparison_replay_mismatch")
    if len(comparison["rows"]) > MAX_ROWS:
        raise ValueError("stockout_training_row_limit")
    policy = SplitPolicy.model_validate(split["descriptor"]["policy"])
    indexed = {key(r): r for r in comparison["rows"]}
    forecast = {key(p): p for p in upstream["points"]}
    rows: dict[str, list[dict[str, Any]]] = {}
    outcomes: dict[str, list[int]] = {}
    lineage = []
    coverage = {}
    for role in ROLES[:-1]:
        # This helper replays the entire split and rejects a resealed test -> train move.
        points = development_labels(split, features, labels, role=role)  # type: ignore[arg-type]
        points.sort(key=lambda p: (p.product_id, p.stock_location_id, p.as_of))
        selected, target = [], []
        for point in points:
            raw = point.model_dump(mode="json")
            row = indexed[key(raw)]
            if row["status"] != "eligible" or point.incident_stockout not in (0, 1):
                raise ValueError("stockout_training_ineligible_or_unknown_label")
            category, source_hash = category_at(catalog, point.product_id, point.as_of)
            selected.append(
                dict(
                    product_id=point.product_id,
                    stock_location_id=point.stock_location_id,
                    as_of=raw["as_of"],
                    category_id=category,
                    values={**row["base"], **row["upstream"]},
                )
            )
            target.append(point.incident_stockout)
            lineage.append([role, *key(raw), category, source_hash])
        rows[role], outcomes[role] = selected, target
        available = sum(forecast[key(r)]["status"] == "available" for r in selected)
        coverage[role] = dict(eligible=len(selected), forecast_available=available)
    coverage["final_test"] = dict(
        eligible_membership_only=sum(
            m["eligible"] and m["role"] == "test" for m in split["membership"]
        ),
        outcomes_evaluated=False,
    )
    coverage["raw_global_upstream_forecast_ready"] = upstream["report"]["upstream_forecast_ready"]
    if any(not rows[r] or set(outcomes[r]) != {0, 1} for r in ROLES[:-1]):
        raise ValueError("stockout_training_requires_both_development_classes")
    return DevelopmentData(
        rows=rows,
        outcomes=outcomes,
        parents=dict(
            source_dataset_id=features["descriptor"]["source_dataset_id"],
            curated_dataset_id=features["descriptor"]["curated_dataset_id"],
            feature_dataset_id=features["feature_dataset_id"],
            upstream_id=upstream["upstream_id"],
            comparison_id=comparison["comparison_id"],
            label_dataset_id=labels["label_dataset_id"],
            split_id=split["split_id"],
        ),
        split_policy=policy,
        coverage=coverage,
        categorical_lineage_sha256=digest(lineage),
    )


def keys_sha256(rows: list[dict[str, Any]]) -> str:
    return digest([key(r) for r in rows])


def labels_sha256(rows: list[dict[str, Any]], outcomes: list[int]) -> str:
    return digest([[*key(r), y] for r, y in zip(rows, outcomes, strict=True)])
