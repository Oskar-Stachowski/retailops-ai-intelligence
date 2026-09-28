"""Closed metadata/record graph validation; no file reads or training execution."""

from datetime import timedelta
from typing import Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Versioned, end_of_day
from retailops_ai.data_contracts.dataset import DatasetManifest
from retailops_ai.data_contracts.feature import FeatureRecord
from retailops_ai.data_contracts.identity import canonical_sha256, logical_rows_sha256
from retailops_ai.data_contracts.label import LabelRecord
from retailops_ai.data_contracts.model import ModelRecord
from retailops_ai.data_contracts.prediction import PredictionRecord
from retailops_ai.data_contracts.run import MLRunRecord
from retailops_ai.data_contracts.split import SplitRecord
from retailops_ai.data_contracts.tool import ToolResult


class ContractBundle(Versioned):
    contract_type: Literal["bundle"]
    datasets: list[DatasetManifest] = Field(min_length=1, max_length=32)
    features: list[FeatureRecord] = Field(max_length=1000)
    labels: list[LabelRecord] = Field(max_length=1000)
    splits: list[SplitRecord] = Field(max_length=32)
    models: list[ModelRecord] = Field(max_length=32)
    runs: list[MLRunRecord] = Field(max_length=100)
    predictions: list[PredictionRecord] = Field(max_length=1000)
    tool_results: list[ToolResult] = Field(max_length=50)

    @model_validator(mode="after")
    def closed_lineage(self) -> Self:
        datasets = {d.dataset_id: d for d in self.datasets}
        models = {m.model_id: m for m in self.models}
        runs = {r.run_id: r for r in self.runs}
        splits = {s.split_id: s for s in self.splits}
        if (
            len(datasets) != len(self.datasets)
            or len(models) != len(self.models)
            or len(runs) != len(self.runs)
            or len(splits) != len(self.splits)
        ):
            raise ValueError("duplicate_bundle_identity")

        def dataset(identity: str, role: str) -> DatasetManifest:
            item = datasets.get(identity)
            if item is None or item.identity.role != role:
                raise ValueError("missing_or_wrong_role_dataset")
            return item

        def data_lineage(source: str, curated: str) -> None:
            if dataset(source, "source").classification != "facts":
                raise ValueError("training_input_is_not_observable_facts")
            parent = dataset(curated, "curated").identity.parents.source_dataset_id
            if parent != source:
                raise ValueError("curated_source_lineage_mismatch")

        for item in self.datasets:
            parents = item.identity.parents
            for name, role in [
                ("source_dataset_id", "source"),
                ("curated_dataset_id", "curated"),
                ("feature_set_id", "features"),
                ("label_dataset_id", "labels"),
                ("split_id", "split"),
            ]:
                identity = getattr(parents, name)
                if identity is not None:
                    dataset(identity, role)
            if parents.source_dataset_id is not None and parents.curated_dataset_id is not None:
                data_lineage(parents.source_dataset_id, parents.curated_dataset_id)
            if parents.model_id is not None and parents.model_id not in models:
                raise ValueError("prediction_manifest_model_missing")
            if parents.inference_run_id is not None and parents.inference_run_id not in runs:
                raise ValueError("prediction_manifest_run_missing")

        records: list[FeatureRecord | LabelRecord] = [*self.features, *self.labels]
        for record in records:
            role = "features" if isinstance(record, FeatureRecord) else "labels"
            identity = (
                record.feature_set_id
                if isinstance(record, FeatureRecord)
                else record.label_dataset_id
            )
            parent = dataset(identity, role).identity.parents
            data_lineage(record.lineage.source_dataset_id, record.lineage.curated_dataset_id)
            if (
                record.lineage.source_dataset_id != parent.source_dataset_id
                or record.lineage.curated_dataset_id != parent.curated_dataset_id
            ):
                raise ValueError("record_dataset_lineage_mismatch")

        for split_record in self.splits:
            parent = dataset(split_record.split_id, "split").identity.parents
            if (
                split_record.lineage.source_dataset_id != parent.source_dataset_id
                or split_record.lineage.curated_dataset_id != parent.curated_dataset_id
                or split_record.feature_set_id != parent.feature_set_id
                or split_record.label_dataset_id != parent.label_dataset_id
            ):
                raise ValueError("split_lineage_mismatch")

        for model in self.models:
            split = splits.get(model.split_id)
            run = runs.get(model.training_run_id)
            if (
                split is None
                or run is None
                or run.run_type != "training"
                or run.status != "succeeded"
            ):
                raise ValueError("model_requires_succeeded_training_run_and_split")
            if (
                model.training_lineage != split.lineage
                or model.feature_set_id != split.feature_set_id
                or model.label_dataset_id != split.label_dataset_id
            ):
                raise ValueError("model_split_lineage_mismatch")
            if (
                run.input_ref.source_dataset_id != model.training_lineage.source_dataset_id
                or run.input_ref.curated_dataset_id != model.training_lineage.curated_dataset_id
                or run.input_ref.feature_set_id != model.feature_set_id
                or run.input_ref.label_dataset_id != model.label_dataset_id
                or run.input_ref.split_id != model.split_id
                or run.input_ref.as_of_time != model.training_cutoff
                or run.output_ref is None
                or run.output_ref.artifact_id != model.model_id
            ):
                raise ValueError("model_training_run_lineage_mismatch")
            train_outcome_end = end_of_day(split.train.end + timedelta(days=split.max_horizon_days))
            selection_window = split.calibration or split.validation
            selection_outcome_end = end_of_day(
                selection_window.end + timedelta(days=split.max_horizon_days)
            )
            if (
                model.training_cutoff < train_outcome_end
                or model.selection_cutoff < selection_outcome_end
                or model.selection_cutoff
                >= end_of_day(split.test.start).replace(hour=0, minute=0, second=0)
            ):
                raise ValueError("model_cutoff_uses_test_or_immature_labels")
            for label in self.labels:
                if (
                    label.label_dataset_id == model.label_dataset_id
                    and label.status == "eligible"
                    and split.train.start <= label.key.forecast_origin.date() <= split.train.end
                    and label.label_available_at is not None
                    and label.label_available_at > model.training_cutoff
                ):
                    raise ValueError("training_label_not_available_at_cutoff")

        for run in self.runs:
            source, curated = run.input_ref.source_dataset_id, run.input_ref.curated_dataset_id
            data_lineage(source, curated)
            parents = dataset(run.input_ref.feature_set_id, "features").identity.parents
            if parents.source_dataset_id != source or parents.curated_dataset_id != curated:
                raise ValueError("run_feature_lineage_mismatch")
            if run.run_type == "training":
                split = splits.get(run.input_ref.split_id or "")
                if (
                    split is None
                    or split.lineage.source_dataset_id != source
                    or split.lineage.curated_dataset_id != curated
                    or split.feature_set_id != run.input_ref.feature_set_id
                    or split.label_dataset_id != run.input_ref.label_dataset_id
                ):
                    raise ValueError("training_run_split_mismatch")
                if run.output_ref is not None and run.output_ref.artifact_id not in models:
                    raise ValueError("training_run_output_model_missing")
            elif (
                run.resolved_model is None
                or models.get(run.resolved_model.model_id) != run.resolved_model
            ):
                raise ValueError("run_resolved_model_mismatch")
            if run.output_ref is not None and run.output_ref.kind == "predictions":
                dataset(run.output_ref.artifact_id, "predictions")

        for prediction in self.predictions:
            run = runs.get(prediction.inference_run_id)
            parents = dataset(prediction.prediction_dataset_id, "predictions").identity.parents
            if (
                run is None
                or run.run_type != "forecast_batch"
                or run.status != "succeeded"
                or run.output_ref is None
                or run.output_ref.artifact_id != prediction.prediction_dataset_id
                or run.resolved_model != prediction.model
                or run.input_ref.as_of_time != prediction.key.forecast_origin
            ):
                raise ValueError("prediction_run_model_mismatch")
            if (
                run.started_at is None
                or run.completed_at is None
                or not run.started_at <= prediction.generated_at <= run.completed_at
            ):
                raise ValueError("prediction_generation_outside_run")
            if (
                prediction.lineage.source_dataset_id != run.input_ref.source_dataset_id
                or prediction.lineage.curated_dataset_id != run.input_ref.curated_dataset_id
                or prediction.feature_set_id != run.input_ref.feature_set_id
                or prediction.feature_set_id != parents.feature_set_id
                or prediction.lineage.source_dataset_id != parents.source_dataset_id
                or prediction.lineage.curated_dataset_id != parents.curated_dataset_id
                or prediction.model.model_id != parents.model_id
                or prediction.inference_run_id != parents.inference_run_id
            ):
                raise ValueError("prediction_dataset_lineage_mismatch")
            if not any(
                feature.feature_set_id == prediction.feature_set_id
                and feature.key == prediction.key
                for feature in self.features
            ):
                raise ValueError("prediction_feature_key_missing")

        for item in self.datasets:
            role = item.identity.role
            payloads: list[dict[str, object]]
            if role == "features":
                payloads = [
                    f.model_dump(mode="json", exclude={"feature_set_id"})
                    for f in self.features
                    if f.feature_set_id == item.dataset_id
                ]
            elif role == "labels":
                payloads = [
                    label.model_dump(mode="json", exclude={"label_dataset_id"})
                    for label in self.labels
                    if label.label_dataset_id == item.dataset_id
                ]
            elif role == "predictions":
                payloads = [
                    p.identity_payload()
                    for p in self.predictions
                    if p.prediction_dataset_id == item.dataset_id
                ]
            elif role == "split":
                split = splits.get(item.dataset_id)
                if split is None or item.identity.logical_content_sha256 != canonical_sha256(
                    split.identity_payload()
                ):
                    raise ValueError("split_logical_content_mismatch")
                if sum(a.rows for a in item.artifacts) != 1:
                    raise ValueError("split_row_count_mismatch")
                continue
            else:
                continue  # Source/curated payloads and bytes are checked by the stage-03 importer.
            if payloads:
                field = "forecast_origin" if role == "features" else "target_date"
                dates = []
                for payload in payloads:
                    key_value = payload["key"]
                    if not isinstance(key_value, dict):
                        raise ValueError("bundle_forecast_key_missing")
                    dates.append(str(key_value[field])[:10])
                dates.sort()
                if (
                    min(a.date_from for a in item.artifacts).isoformat() != dates[0]
                    or max(a.date_to for a in item.artifacts).isoformat() != dates[-1]
                ):
                    raise ValueError("bundle_record_date_range_mismatch")
            if sum(a.rows for a in item.artifacts) != len(payloads):
                raise ValueError("bundle_record_count_mismatch")
            if item.identity.logical_content_sha256 != logical_rows_sha256(payloads, keys=["key"]):
                raise ValueError("bundle_logical_content_mismatch")

        for result in self.tool_results:
            for tool_prediction in result.items:
                if tool_prediction not in self.predictions:
                    raise ValueError("tool_result_is_not_persisted_bundle_output")
        return self
