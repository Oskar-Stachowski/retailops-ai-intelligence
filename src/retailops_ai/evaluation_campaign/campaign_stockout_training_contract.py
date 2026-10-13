"""Explicit full-population model state; earlier AI08 wire and bounds stay intact."""

from typing import Annotated, Literal

from pydantic import Field

from retailops_ai.data_contracts.common import FalseFlag, Sha256
from retailops_ai.stockout_selection.contract import ConditionalRiskPipeline, ConditionalSigmoid
from retailops_ai.stockout_training.contract import Preprocessing, RiskPipeline

MAX_STOCKOUT_TRAINING_ROWS = 1_000_000


class CampaignStockoutPreprocessing(Preprocessing):
    version: Literal["ai09-full-stockout-preprocessing-1.0.0"] = (
        "ai09-full-stockout-preprocessing-1.0.0"
    )
    train_rows: Annotated[int, Field(ge=2, le=MAX_STOCKOUT_TRAINING_ROWS)]
    train_keys_sha256: Sha256


class CampaignStockoutRiskPipeline(RiskPipeline):
    version: Literal["ai09-full-stockout-risk-pipeline-1.0.0"] = (
        "ai09-full-stockout-risk-pipeline-1.0.0"
    )
    preprocessing: CampaignStockoutPreprocessing
    train_labels_sha256: Sha256
    sigmoid: None = None
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False


class CampaignStockoutConditionalSigmoid(ConditionalSigmoid):
    version: Literal["ai09-full-stockout-conditional-sigmoid-1.0.0"] = (
        "ai09-full-stockout-conditional-sigmoid-1.0.0"
    )
    calibration_rows: Annotated[int, Field(ge=20, le=MAX_STOCKOUT_TRAINING_ROWS)]


class CampaignStockoutConditionalRiskPipeline(ConditionalRiskPipeline):
    version: Literal["ai09-full-stockout-conditional-risk-pipeline-1.0.0"] = (
        "ai09-full-stockout-conditional-risk-pipeline-1.0.0"  # type: ignore[assignment]
    )
    base: CampaignStockoutRiskPipeline
    calibrator: CampaignStockoutConditionalSigmoid
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False
