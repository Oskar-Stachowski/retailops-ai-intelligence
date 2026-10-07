"""Bounded physical inference scope, independent of prepared data and model state."""

from typing import Self

from pydantic import Field, field_validator, model_validator

from retailops_ai.data_contracts.common import Contract, Symbol


class PhysicalScope(Contract):
    product_ids: tuple[Symbol, ...] = Field(min_length=1, max_length=20)
    stock_location_ids: tuple[Symbol, ...] = Field(min_length=1, max_length=5)

    @field_validator("product_ids", "stock_location_ids", mode="before")
    @classmethod
    def wire_arrays(cls, value: object) -> object:
        # FastAPI decodes JSON before validation; retain strict member types.
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def unique(self) -> Self:
        if any(
            len(values) != len(set(values))
            for values in (self.product_ids, self.stock_location_ids)
        ):
            raise ValueError("stockout_inference_duplicate_scope")
        return self
