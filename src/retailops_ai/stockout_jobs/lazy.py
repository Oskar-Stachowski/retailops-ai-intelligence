"""Construct storage/scoring adapters only when an authorized stockout request uses them."""

from typing import Literal

from sqlalchemy import Engine

from retailops_ai.domain.access import Principal
from retailops_ai.stockout_jobs.ports import StockoutAdministration, StockoutReader
from retailops_ai.stockout_jobs.public_contracts import StockoutJobRun, StockoutRequest
from retailops_ai.stockout_jobs.read_contracts import (
    StockoutAttempts,
    StockoutQuery,
    StockoutRisk,
    StockoutRiskPage,
)


class LazyStockoutAdministration:
    def __init__(self, engine: Engine, environment: Literal["local", "test"]) -> None:
        self.engine, self.environment = engine, environment

    def _backend(self) -> StockoutAdministration:
        from retailops_ai.stockout_jobs.queue import PostgresStockoutQueue
        from retailops_ai.stockout_jobs.reader import PostgresStockoutAdministration

        return PostgresStockoutAdministration(PostgresStockoutQueue(self.engine, self.environment))

    def submit(self, request: StockoutRequest, principal: Principal, key: str) -> StockoutJobRun:
        return self._backend().submit(request, principal, key)

    def get(self, run_id: str, principal: Principal) -> StockoutJobRun:
        return self._backend().get(run_id, principal)

    def attempts(self, run_id: str, principal: Principal) -> StockoutAttempts:
        return self._backend().attempts(run_id, principal)


class LazyStockoutReader:
    def __init__(self, engine: Engine, environment: Literal["local", "test"]) -> None:
        self.engine, self.environment = engine, environment

    def _backend(self) -> StockoutReader:
        from retailops_ai.stockout_jobs.reader import PostgresStockoutReader

        return PostgresStockoutReader(self.engine, self.environment)

    def list(self, query: StockoutQuery, principal: Principal) -> StockoutRiskPage:
        return self._backend().list(query, principal)

    def get(self, risk_id: str, principal: Principal) -> StockoutRisk:
        return self._backend().get(risk_id, principal)
