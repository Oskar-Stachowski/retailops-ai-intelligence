"""Public stockout administration and read ports, independent of storage and training."""

from typing import Protocol

from retailops_ai.domain.access import Principal
from retailops_ai.stockout_jobs.public_contracts import StockoutJobRun, StockoutRequest
from retailops_ai.stockout_jobs.read_contracts import (
    StockoutAttempts,
    StockoutQuery,
    StockoutRisk,
    StockoutRiskPage,
)


class StockoutAdministration(Protocol):
    def submit(
        self, request: StockoutRequest, principal: Principal, key: str
    ) -> StockoutJobRun: ...
    def get(self, run_id: str, principal: Principal) -> StockoutJobRun: ...
    def attempts(self, run_id: str, principal: Principal) -> StockoutAttempts: ...


class StockoutReader(Protocol):
    def list(self, query: StockoutQuery, principal: Principal) -> StockoutRiskPage: ...
    def get(self, risk_id: str, principal: Principal) -> StockoutRisk: ...
