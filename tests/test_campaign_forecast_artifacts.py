"""Resealing outer file hashes never makes an incompatible Keras signature valid."""

import json

import pytest
import yaml
from test_campaign_fit import encoding
from test_campaign_fit_data import fit_plan

from retailops_ai.evaluation_campaign.campaign_forecast_artifacts import verify_keras_signature
from retailops_ai.source_snapshot.files import SnapshotError


def signature(plan, state):
    return {
        "flavors": {"keras": {"keras_backend": "tensorflow"}},
        "metadata": {
            "retailops.plan_sha256": plan.content_sha256(),
            "retailops.scope": "ai09-campaign-development",
        },
        "signature": {
            "inputs": json.dumps(
                [
                    {
                        "type": "tensor",
                        "tensor-spec": {"dtype": "float32", "shape": [-1, state.tensorflow_width]},
                    }
                ]
            ),
            "outputs": json.dumps(
                [{"type": "tensor", "tensor-spec": {"dtype": "float32", "shape": [-1, 14, 2]}}]
            ),
        },
    }


def test_supported_signature_is_checked_without_importing_tensorflow(tmp_path):
    plan = fit_plan().model_copy(update={"family": "tensorflow"})
    state = encoding()
    (tmp_path / "keras").mkdir(mode=0o700)
    (tmp_path / "keras/MLmodel").write_text(yaml.safe_dump(signature(plan, state)))
    verify_keras_signature(tmp_path, plan, state)


@pytest.mark.parametrize(
    "fault",
    [
        "width",
        "dtype",
        "horizon",
        "heads",
        "missing_input",
        "wrong_plan",
        "backend",
        "missing_flavor",
        "invalid_yaml",
        "invalid_json",
        "metadata_type",
    ],
)
def test_incompatible_signature_and_metadata_are_rejected_even_without_checksum_guard(
    tmp_path, fault
):
    plan = fit_plan().model_copy(update={"family": "tensorflow"})
    state = encoding()
    body = signature(plan, state)
    if fault in ("width", "dtype", "missing_input"):
        spec = json.loads(body["signature"]["inputs"])
        if fault == "width":
            spec[0]["tensor-spec"]["shape"] = [-1, 99999]
        elif fault == "dtype":
            spec[0]["tensor-spec"]["dtype"] = "float64"
        else:
            spec = []
        body["signature"]["inputs"] = json.dumps(spec)
    elif fault in ("horizon", "heads"):
        body["signature"]["outputs"] = json.dumps(
            [
                {
                    "tensor-spec": {
                        "dtype": "float32",
                        "shape": [
                            -1,
                            13 if fault == "horizon" else 14,
                            1 if fault == "heads" else 2,
                        ],
                    }
                }
            ]
        )
    elif fault == "wrong_plan":
        body["metadata"]["retailops.plan_sha256"] = "0" * 64
    elif fault == "backend":
        body["flavors"]["keras"]["keras_backend"] = "jax"
    elif fault == "missing_flavor":
        body["flavors"] = {}
    elif fault == "invalid_json":
        body["signature"]["inputs"] = "{"
    elif fault == "metadata_type":
        body = ["not an MLmodel"]
    (tmp_path / "keras").mkdir(mode=0o700)
    raw = "signature: [" if fault == "invalid_yaml" else yaml.safe_dump(body)
    (tmp_path / "keras/MLmodel").write_text(raw)
    with pytest.raises(SnapshotError, match="campaign_forecast_keras_signature_mismatch"):
        verify_keras_signature(tmp_path, plan, state)
