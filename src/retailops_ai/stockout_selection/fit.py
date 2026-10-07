"""Full TRAIN base, later TUNE calibrator, later CAL selection; TEST is never read."""

from typing import Any

from pydantic import TypeAdapter

from retailops_ai.data_contracts.common import UtcTime
from retailops_ai.stockout.split import SplitPolicy, key
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_selection.contract import SelectionPolicy
from retailops_ai.stockout_selection.gates import quality_gates
from retailops_ai.stockout_selection.pipeline import fit_conditional, predict_conditional
from retailops_ai.stockout_training.contract import DEFAULT_POLICY
from retailops_ai.stockout_training.development import FAMILIES, VARIANTS
from retailops_ai.stockout_training.evaluation import segmented_metrics
from retailops_ai.stockout_training.inputs import DevelopmentData, keys_sha256, labels_sha256
from retailops_ai.stockout_training.pipeline import fit_model, predict


def validate_roles(data: DevelopmentData, membership: list[dict[str, Any]]) -> None:
    split = SplitPolicy.model_validate_json(data.split_policy.model_dump_json())
    clock = TypeAdapter(UtcTime).validate_python
    if set(data.rows) != {"train", "tune", "calibration"} or set(data.outcomes) != set(data.rows):
        raise ValueError("stockout_selection_development_only")
    if len(membership) > 10000:
        raise ValueError("stockout_selection_membership_limit")
    indexed = {key(m): m for m in membership}
    if len(indexed) != len(membership):
        raise ValueError("stockout_selection_duplicate_membership")
    seen: set[tuple[str, str, str]] = set()
    for role, lower, upper in (
        ("train", split.start_at, split.train_until),
        ("tune", split.train_until, split.tune_until),
        ("calibration", split.tune_until, split.calibration_until),
    ):
        rows, ys = data.rows[role], data.outcomes[role]
        if not rows or len(rows) != len(ys) or set(ys) != {0, 1}:
            raise ValueError("stockout_selection_role_rows_or_classes")
        for r, y in zip(rows, ys, strict=True):
            k = key(r)
            m = indexed.get(k)
            if (
                k in seen
                or m is None
                or m["role"] != role
                or m["eligible"] is not True
                or type(y) is not int
                or not lower <= clock(r["as_of"]) < upper
                or max(
                    clock(m["label_available_at"]),
                    clock(m["window_end_at"]),
                )
                >= upper
            ):
                raise ValueError("stockout_selection_membership_chronology_or_overlap")
            seen.add(k)
    expected = {key(m) for m in membership if m["eligible"] is True and m["role"] in data.rows}
    if seen != expected:
        raise ValueError("stockout_selection_incomplete_eligible_development_membership")


def fit_selection(
    data: DevelopmentData, membership: list[dict[str, Any]], policy: SelectionPolicy
) -> dict[str, Any]:
    policy = SelectionPolicy.model_validate_json(policy.model_dump_json())
    validate_roles(data, membership)
    base_rows, base_y = data.rows["train"], data.outcomes["train"]
    cal_rows, cal_y = data.rows["tune"], data.outcomes["tune"]
    select_rows, select_y = data.rows["calibration"], data.outcomes["calibration"]
    split = data.split_policy
    prevalence = sum(base_y) / len(base_y)
    categories = {r["category_id"] for rows in data.rows.values() for r in rows}
    locations = {r["stock_location_id"] for rows in data.rows.values() for r in rows}
    bases: dict[str, Any] = {}
    comparison: dict[str, Any] = {}
    calibrated: dict[str, Any] = {}
    rejected: dict[str, str] = {}
    selection_keys: dict[str, tuple[int, float, float, str, float]] = {}
    for family in FAMILIES:
        for variant in VARIANTS:
            name = family + ":" + variant
            base = fit_model(
                base_rows,
                base_y,
                family=family,
                variant=variant,
                fit_known_at=split.train_until,
                policy=DEFAULT_POLICY,
            )
            bases[name] = base.model_dump(mode="json")
            raw_metrics = segmented_metrics(
                select_rows,
                select_y,
                list(predict(base, select_rows, calibrated=False)),
                DEFAULT_POLICY,
                train_prevalence=prevalence,
            )
            for C in policy.calibrator_C_grid:
                candidate = name + ":conditional-C" + str(C)
                try:
                    model = fit_conditional(
                        base, cal_rows, cal_y, C=C, fit_known_at=split.tune_until
                    )
                except ValueError as exc:
                    if str(exc) != "stockout_conditional_nonpositive_raw_score_slope":
                        raise
                    rejected[candidate] = str(exc)
                    continue
                metrics = segmented_metrics(
                    select_rows,
                    select_y,
                    predict_conditional(model, select_rows).tolist(),
                    DEFAULT_POLICY,
                    train_prevalence=prevalence,
                )
                gates = quality_gates(
                    metrics,
                    expected_categories=categories,
                    expected_locations=locations,
                    policy=policy.requirements,
                )
                failures = sum(g["status"] != "passed" for g in gates["segments"].values())
                all_metric = metrics["all"]
                selection_keys[candidate] = (
                    failures,
                    all_metric["brier"],
                    -all_metric["average_precision"],
                    name,
                    C,
                )
                comparison[candidate] = dict(
                    base_model=name,
                    C=C,
                    raw=raw_metrics,
                    conditional=metrics,
                    development_selection_gates=gates,
                    calibrator=model.calibrator.model_dump(mode="json"),
                )
                calibrated[candidate] = model
    if not calibrated:
        raise ValueError("stockout_selection_no_monotone_calibrated_candidate")
    selected = min(selection_keys, key=selection_keys.__getitem__)
    chosen = calibrated[selected]
    selected_state = chosen.model_dump(mode="json")
    content = dict(
        base_pipelines=bases,
        comparison=comparison,
        rejected_candidates=rejected,
        selected=selected,
        selected_pipeline=selected_state,
        selected_model_id="risk-model-sha256-" + digest(selected_state),
        selected_calibrator_sha256=digest(chosen.calibrator.model_dump(mode="json")),
        selected_development_gates=comparison[selected]["development_selection_gates"],
        roles={
            name: dict(
                original_role=role,
                rows=len(data.rows[role]),
                positives=sum(data.outcomes[role]),
                negatives=data.outcomes[role].count(0),
                keys_sha256=keys_sha256(data.rows[role]),
                outcomes_sha256=labels_sha256(data.rows[role], data.outcomes[role]),
            )
            for name, role in (
                ("base_train", "train"),
                ("calibration_fit", "tune"),
                ("development_selection", "calibration"),
            )
        },
        roles_disjoint=True,
        selection_known_at=split.calibration_until.isoformat().replace("+00:00", "Z"),
        metrics_interpretation=policy.selection_metrics,
        independent_quality_accepted=False,
        final_test_outcomes_evaluated=False,
        model_promoted=False,
        model_ready=False,
        limitations=[
            "Synthetic bounded intermediate profile; not qualified full ai-dev/ai-training.",
            "CAL is used for selection; its gates do not prove independent final quality.",
            "Earlier CAL outcomes were reviewed during development; no unbiased claim.",
            "Category and location offsets are fitted only on earlier TUNE outcomes.",
            "Positive raw-score slope is monotone within a fixed PIT group, not across groups.",
            "Thresholds and capacity require a separately reviewed development policy.",
            "All final holdouts remain unopened pending a frozen approved campaign.",
        ],
    )
    descriptor = dict(
        schema_version="stockout-development-selection-2.0.0",
        parents=data.parents,
        policy=policy.model_dump(mode="json"),
        split_policy=split.model_dump(mode="json"),
        content_sha256=digest(content),
    )
    return dict(
        selection_id="stockout-selection-sha256-" + digest(descriptor),
        descriptor=descriptor,
        content=content,
    )
