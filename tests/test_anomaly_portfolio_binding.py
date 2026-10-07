"""An explicit minor binding expands the parent census without weakening v2."""

import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from retailops_ai.full_raw_dq.contract import Binding, PortfolioBinding, parse_binding


def binding_payload():
    archive = Path(__file__).resolve().parents[1] / "data/fixtures/full-raw-dq-v2.zip"
    with ZipFile(archive) as value:
        return json.loads(value.read("demand/capture/source_binding.json"))


def test_old_binding_preserves_4096_limit_and_large_binding_has_separate_version():
    payload = binding_payload()
    assert isinstance(parse_binding(json.dumps(payload).encode()), Binding)
    payload["source_event_count"] = 6836
    payload["source_sales_count"] = 5887
    payload["source_return_count"] = 949
    with pytest.raises(ValidationError):
        parse_binding(json.dumps(payload).encode())
    payload["contract_version"] = "raw-dq-binding-2.1.0"
    assert isinstance(parse_binding(json.dumps(payload).encode()), PortfolioBinding)
    payload["source_event_count"] = 8193
    with pytest.raises(ValidationError):
        parse_binding(json.dumps(payload).encode())
