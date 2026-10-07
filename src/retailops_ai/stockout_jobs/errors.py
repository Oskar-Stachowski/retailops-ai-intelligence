"""Public bounded error vocabulary, without a database or model import."""

from retailops_ai.stockout_jobs.public_contracts import StockoutErrorCode


class StockoutError(ValueError):
    def __init__(self, status: int, code: StockoutErrorCode) -> None:
        super().__init__(code)
        self.status, self.code = status, code
