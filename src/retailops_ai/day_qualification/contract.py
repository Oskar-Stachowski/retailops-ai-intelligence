"""Strict declaration and query contracts; a closure never implies model readiness."""

from typing import Annotated, Literal

from pydantic import Field

from retailops_ai.data_contracts.common import Contract, Sha256, SourceID
from retailops_ai.full_raw_dq.contract import TableIdentity
from retailops_ai.raw_dq.contract import Timestamp

VERSION = "ai-business-day-qualification-1.0.0"
TABLES = (
    "daily_demand_observations",
    "inventory_sales",
    "product_catalog",
    "return_events",
    "return_policies",
)
KEY = ("product_id", "selling_location_id", "channel", "currency")
GRAIN = ("event_type", "business_date", *KEY)
MAX_ROWS = 10000
MAX_BYTES = 32 * 1024**2
ID = Annotated[
    str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]
IDs = Annotated[list[ID], Field(max_length=4096)]
DayDate = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class Day(Contract):
    event_type: Literal["sale_completed", "return_completed"]
    business_date: DayDate
    product_id: ID
    selling_location_id: ID
    channel: Literal["store", "online", "marketplace", "wholesale"]
    currency: Literal["PLN", "EUR"]
    window_end: Timestamp
    known_at: Timestamp
    source_complete: bool
    activity: Literal["open", "closed", "missing"]
    expected_business_ids: IDs
    required_sale_ids: IDs


class CoverageDescriptor(Contract):
    contract_version: Literal["business-day-coverage-1.0.0"]
    policy_version: Literal["synthetic-parent-event-day-close-1.0.0"]
    owner: Literal["retailops-cloud-native-platform"]
    source_dataset_id: SourceID
    source_descriptor_sha256: Sha256
    source_tables: Annotated[dict[str, TableIdentity], Field(min_length=5, max_length=5)]
    return_scope: Literal["purchases_in_parent_source_only"]
    return_closure_basis: Literal["verified_complete_synthetic_export_and_bounded_native_ingestion"]
    missing_grain_policy: Literal["unknown_not_zero"]
    cohort_maturity_is_day_closure: Literal[False]
    transport_durability_proven: Literal[False]
    code_sha256: Sha256
    dependency_sha256: Sha256
    python_version: str
    rows_sha256: Sha256
    row_count: Annotated[int, Field(ge=1, le=MAX_ROWS)]


class Coverage(Contract):
    coverage_id: Annotated[str, Field(pattern=r"^day-coverage-sha256-[0-9a-f]{64}$")]
    descriptor: CoverageDescriptor


class Policy(Contract):
    version: Literal["ai-business-day-qualification-1.0.0"] = "ai-business-day-qualification-1.0.0"
    sales_delay_hours: Annotated[int, Field(ge=0, le=168)] = 24
    returns_delay_hours: Annotated[int, Field(ge=0, le=168)] = 72
    as_of: Timestamp | None = None
    unknown_quarantine_policy: Literal["withhold_all_days_until_attributable"] = (
        "withhold_all_days_until_attributable"
    )
    repaired_fact_policy: Literal["accepted_native_replacement_qualifies_only_later_views"] = (
        "accepted_native_replacement_qualifies_only_later_views"
    )


class Point(Contract):
    event_type: Literal["sale_completed", "return_completed"]
    business_date: DayDate
    product_id: ID
    selling_location_id: ID
    channel: Literal["store", "online", "marketplace", "wholesale"]
    currency: Literal["PLN", "EUR"]
    as_of: Timestamp
    status: Literal[
        "no_declaration",
        "closure_unavailable",
        "source_incomplete",
        "location_closed",
        "dq_unattributed_quarantine",
        "dq_missing_facts",
        "qualified",
    ]
    raw_dq_completeness: Literal["qualified", "not_qualified"] = "not_qualified"
    score_eligible: bool = False
    observed_units: Annotated[int, Field(ge=0)] | None = None
    amount: Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]*)\.[0-9]{2}$")] | None = None
    rejected_units: Annotated[int, Field(ge=0)] | None = None
    missing_fact_keys: list[str] = Field(default_factory=list)

    def model_post_init(self, context: object) -> None:
        values = (self.observed_units, self.amount, self.rejected_units)
        if self.status == "qualified":
            if (
                not self.score_eligible
                or self.raw_dq_completeness != "qualified"
                or any(v is None for v in values)
                or self.missing_fact_keys
            ):
                raise ValueError("invalid_qualified_day_value")
        elif (
            self.score_eligible
            or self.raw_dq_completeness != "not_qualified"
            or any(v is not None for v in values)
        ):
            raise ValueError("unknown_day_cannot_supply_value")
