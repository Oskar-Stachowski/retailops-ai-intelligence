"""Versioned fixed-origin windows with a purge covering the label horizon."""

from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    DataLineage,
    DateWindow,
    FeatureID,
    LabelID,
    SplitID,
    Versioned,
)


class SplitRecord(Versioned):
    contract_type: Literal["split"]
    split_id: SplitID
    lineage: DataLineage
    feature_set_id: FeatureID
    label_dataset_id: LabelID
    protocol: Literal["fixed_origin"]
    max_horizon_days: Annotated[int, Field(ge=1, le=14)]
    purge_days: Annotated[int, Field(ge=1, le=90)]
    train: DateWindow
    validation: DateWindow
    calibration: DateWindow | None
    test: DateWindow

    @model_validator(mode="after")
    def no_window_leakage(self) -> Self:
        if self.purge_days < self.max_horizon_days:
            raise ValueError("purge_shorter_than_label_horizon")
        windows = [self.train, self.validation]
        if self.calibration is not None:
            windows.append(self.calibration)
        windows.append(self.test)
        if any(
            b.start <= a.end + timedelta(days=self.purge_days)
            for a, b in zip(windows, windows[1:], strict=False)
        ):
            raise ValueError("overlapping_split_or_label_windows")
        return self

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"split_id"})
