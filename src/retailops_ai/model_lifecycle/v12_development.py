"""Artifact-specific owner acceptance for local development, independent of quality."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from pydantic import model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag
from retailops_ai.forecast_jobs.v12_executor import development_acceptance_matches
from retailops_ai.model_lifecycle.contracts import Receipt
from retailops_ai.source_snapshot.files import read_bytes

if TYPE_CHECKING:
    from retailops_ai.forecast_jobs.v12_contracts import V12RuntimePin


class V12DevelopmentAcceptance(Contract):
    version: Literal["v12-development-acceptance-1.0.0"] = "v12-development-acceptance-1.0.0"
    scope: Literal["local_development_only"] = "local_development_only"
    decision: Receipt
    production_deployment_authorized: FalseFlag = False
    original_quality_reclassified: FalseFlag = False

    @model_validator(mode="after")
    def fixed_owner_decision(self) -> Self:
        if not development_acceptance_matches(self.model_dump(mode="json")):
            raise ValueError("v12_development_owner_decision_changed")
        return self

    def verify_pin(self, pin: V12RuntimePin) -> None:
        if (
            not development_acceptance_matches(
                self.model_dump(mode="json"),
                pin.run_id,
                pin.manifest.sha256,
            )
            or pin.forecast_model_status != "not_ready"
        ):
            raise ValueError("v12_development_acceptance_wrong_run")


def read_development_acceptance(path: Path) -> V12DevelopmentAcceptance:
    raw = read_bytes(path.parent, path.name)
    return V12DevelopmentAcceptance(
        decision=Receipt(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    )
