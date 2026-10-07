"""Check the real supported Keras signature and frozen metadata before framework loading."""

import importlib
import json
from pathlib import Path

from retailops_ai.evaluation_campaign.campaign_fit_contract import (
    CampaignForecastEncoding,
    CampaignForecastFitPlan,
)
from retailops_ai.source_snapshot.files import SnapshotError, read_bytes


def verify_keras_signature(
    bundle: Path, plan: CampaignForecastFitPlan, encoding: CampaignForecastEncoding
) -> None:
    yaml = importlib.import_module("yaml")
    try:
        metadata = yaml.safe_load(read_bytes(bundle, "keras/MLmodel", 1024**2))
        signature = metadata["signature"]
        inputs = json.loads(signature["inputs"])
        outputs = json.loads(signature["outputs"])
        if (
            plan.family != "tensorflow"
            or "keras" not in metadata["flavors"]
            or metadata["flavors"]["keras"].get("keras_backend") != "tensorflow"
            or metadata["metadata"]
            != {
                "retailops.plan_sha256": plan.content_sha256(),
                "retailops.scope": "ai09-campaign-development",
            }
            or len(inputs) != 1
            or inputs[0].get("tensor-spec")
            != {"dtype": "float32", "shape": [-1, encoding.tensorflow_width]}
            or len(outputs) != 1
            or outputs[0].get("tensor-spec") != {"dtype": "float32", "shape": [-1, 14, 2]}
        ):
            raise SnapshotError("campaign_forecast_keras_signature_mismatch")
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise SnapshotError("campaign_forecast_keras_signature_mismatch") from None
