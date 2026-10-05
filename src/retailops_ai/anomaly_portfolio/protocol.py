"""Explicit final-portfolio windows, separate from the mechanics-only protocol."""

from typing import Literal

from retailops_ai.anomaly_detectors.protocol import Protocol


class PortfolioProtocol(Protocol):
    version: Literal["anomaly-portfolio-protocol-1.0.0"] = "anomaly-portfolio-protocol-1.0.0"  # type: ignore[assignment]
    purpose: Literal["bounded_portfolio_qualification"] = "bounded_portfolio_qualification"  # type: ignore[assignment]
    final_portfolio_test: Literal["reserved_until_selection_frozen"] = (
        "reserved_until_selection_frozen"  # type: ignore[assignment]
    )
