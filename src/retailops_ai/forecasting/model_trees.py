"""Portable numeric tree inference; stored JSON contains data, never executable estimators."""

from typing import Any

import numpy as np
from numpy.typing import NDArray

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifest_contract import FeaturePolicy
from retailops_ai.forecasting.model_contract import (
    ForecastValue,
    LearnedEstimator,
    LearnedTree,
    ModelPipeline,
    TreeNode,
)
from retailops_ai.forecasting.preprocessing import FittedState, transform, transform_inference
from retailops_ai.source_snapshot.files import SnapshotError


def export_estimator(model: Any, family: str, feature_count: int) -> LearnedEstimator:
    trees = []
    if family == "random_forest":
        for estimator in model.estimators_:
            tree = estimator.tree_
            trees.append(
                LearnedTree(
                    nodes=tuple(
                        TreeNode(
                            value=float(tree.value[index, 0, 0]),
                            feature=int(tree.feature[index])
                            if tree.children_left[index] >= 0
                            else None,
                            threshold=float(tree.threshold[index])
                            if tree.children_left[index] >= 0
                            else None,
                            left=int(tree.children_left[index])
                            if tree.children_left[index] >= 0
                            else None,
                            right=int(tree.children_right[index])
                            if tree.children_left[index] >= 0
                            else None,
                        )
                        for index in range(tree.node_count)
                    )
                )
            )
        return LearnedEstimator(
            family="random_forest",
            feature_count=feature_count,
            input_dtype="float32",
            aggregation="mean",
            intercept=0.0,
            trees=tuple(trees),
        )
    if family != "hist_gradient_boosting":
        raise SnapshotError("forecast_model_family_not_allowlisted")
    for iteration in model._predictors:
        if len(iteration) != 1:
            raise SnapshotError("forecast_hgb_not_single_output")
        native = iteration[0].nodes
        if any(native["is_categorical"]):
            raise SnapshotError("forecast_hgb_categorical_nodes_not_in_numeric_recipe")
        trees.append(
            LearnedTree(
                nodes=tuple(
                    TreeNode(
                        value=float(node["value"]),
                        feature=None if node["is_leaf"] else int(node["feature_idx"]),
                        threshold=None if node["is_leaf"] else float(node["num_threshold"]),
                        left=None if node["is_leaf"] else int(node["left"]),
                        right=None if node["is_leaf"] else int(node["right"]),
                    )
                    for node in native
                )
            )
        )
    return LearnedEstimator(
        family="hist_gradient_boosting",
        feature_count=feature_count,
        input_dtype="float64",
        aggregation="baseline_plus_sum",
        intercept=float(model._baseline_prediction[0, 0]),
        trees=tuple(trees),
    )


class TreePredictor:
    def __init__(self, estimator: LearnedEstimator) -> None:
        self.estimator = LearnedEstimator.model_validate_json(estimator.model_dump_json())
        self.trees = [
            (
                np.asarray(
                    [node.feature if node.feature is not None else -1 for node in tree.nodes],
                    dtype=np.int64,
                ),
                np.asarray(
                    [node.threshold if node.threshold is not None else 0.0 for node in tree.nodes]
                ),
                np.asarray(
                    [node.left if node.left is not None else 0 for node in tree.nodes],
                    dtype=np.int64,
                ),
                np.asarray(
                    [node.right if node.right is not None else 0 for node in tree.nodes],
                    dtype=np.int64,
                ),
                np.asarray([node.value for node in tree.nodes]),
            )
            for tree in self.estimator.trees
        ]

    def matrix(self, values: NDArray[np.float64]) -> NDArray[np.float64]:
        if (
            values.ndim != 2
            or values.shape[1] != self.estimator.feature_count
            or values.shape[0] > 50000
            or not np.isfinite(values).all()
        ):
            raise SnapshotError("forecast_portable_matrix_invalid")
        converted = np.asarray(values, dtype=self.estimator.input_dtype)
        if not np.isfinite(converted).all():
            raise SnapshotError("forecast_portable_matrix_dtype_overflow")
        result = np.full(values.shape[0], self.estimator.intercept, dtype=np.float64)
        for features, thresholds, left, right, leaf_values in self.trees:
            indices = np.zeros(values.shape[0], dtype=np.int64)
            for _ in range(len(features)):
                active = np.flatnonzero(features[indices] >= 0)
                if not len(active):
                    break
                current = indices[active]
                indices[active] = np.where(
                    converted[active, features[current]] <= thresholds[current],
                    left[current],
                    right[current],
                )
            else:
                raise SnapshotError("forecast_portable_tree_did_not_terminate")
            result += leaf_values[indices]
        if self.estimator.aggregation == "mean":
            result /= len(self.trees)
        if not np.isfinite(result).all():
            raise SnapshotError("forecast_portable_prediction_nonfinite")
        return np.maximum(result, 0.0)


class ForecastAdapter:
    def __init__(self, pipeline: ModelPipeline) -> None:
        self.pipeline = ModelPipeline.model_validate_json(pipeline.model_dump_json())
        descriptor = self.pipeline.descriptor.preprocessing
        self.state = FittedState(
            preprocessing_id="forecast-preprocessing-sha256-"
            + canonical_sha256(descriptor.model_dump(mode="json")),
            descriptor=descriptor,
            generated_at=self.pipeline.generated_at,
        )
        self.predictor = TreePredictor(self.pipeline.descriptor.estimator)

    def predict(self, rows: list[InputRow], *, feature_set_id: str) -> tuple[float, ...]:
        return self._predict(rows, feature_set_id=feature_set_id, train_diagnostic=False)

    def forecast(self, rows: list[InputRow], *, feature_set_id: str) -> tuple[ForecastValue, ...]:
        from retailops_ai.data_contracts.common import ForecastKey

        quantities = self.predict(rows, feature_set_id=feature_set_id)
        return tuple(
            ForecastValue(
                **row.model_dump(include=set(ForecastKey.model_fields)),
                model_id=self.pipeline.model_id,
                predicted_units=quantity,
            )
            for row, quantity in zip(rows, quantities, strict=True)
        )

    def diagnose_train(self, rows: list[InputRow], *, feature_set_id: str) -> tuple[float, ...]:
        return self._predict(rows, feature_set_id=feature_set_id, train_diagnostic=True)

    def infer(self, rows: list[InputRow], *, policy: FeaturePolicy) -> tuple[float, ...]:
        """Portable inference on new verified data with the frozen training recipe."""
        if len(rows) > 256:
            raise SnapshotError("forecast_adapter_batch_or_feature_binding_invalid")
        if policy != self.state.descriptor.policy:
            raise SnapshotError("forecast_inference_feature_policy_mismatch")
        return self._values(rows, train_diagnostic=False, inference_policy=policy)

    def _predict(
        self, rows: list[InputRow], *, feature_set_id: str, train_diagnostic: bool
    ) -> tuple[float, ...]:
        if not rows:
            return ()
        if len(rows) > 256 or feature_set_id != self.pipeline.descriptor.feature_set_id:
            raise SnapshotError("forecast_adapter_batch_or_feature_binding_invalid")
        return self._values(rows, train_diagnostic=train_diagnostic)

    def _values(
        self,
        rows: list[InputRow],
        *,
        train_diagnostic: bool,
        inference_policy: FeaturePolicy | None = None,
    ) -> tuple[float, ...]:
        if not rows:
            return ()
        safe = [InputRow.model_validate_json(row.model_dump_json()) for row in rows]
        fold = self.state.descriptor.fold
        policy = self.state.descriptor.policy
        if any(
            row.history_active_days < policy.minimum_active_history_days
            or row.history_known_days < policy.minimum_known_history_days
            or not row.target_calendar_eligible
            for row in safe
        ):
            raise SnapshotError("forecast_adapter_insufficient_history_or_closed_target")
        if any(
            (fold.role(row.forecast_origin.date()) != "train")
            if train_diagnostic
            else (row.forecast_origin <= fold.training_cutoff)
            for row in safe
        ):
            raise SnapshotError("forecast_model_not_known_at_origin")
        matrix = np.asarray(
            [
                (
                    *(
                        transform(
                            row, self.state, feature_set_id=self.pipeline.descriptor.feature_set_id
                        )
                        if inference_policy is None
                        else transform_inference(row, self.state, policy=inference_policy)
                    ),
                    float(row.horizon_days),
                )
                for row in safe
            ],
            dtype=np.float64,
        )
        return tuple(float(value) for value in self.predictor.matrix(matrix))
