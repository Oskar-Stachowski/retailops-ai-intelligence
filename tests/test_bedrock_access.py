import json

import pytest
from botocore.exceptions import ClientError
from test_agent_chat import PRIVATE
from test_bedrock_chat import real_settings, smoke_args, smoke_inputs

from retailops_ai.adapters.bedrock_access import inspect_model_access
from retailops_ai.cli import main


class Access:
    def __init__(self):
        self.availability = {
            "agreementAvailability": {"status": "AVAILABLE", "errorMessage": PRIVATE},
            "authorizationStatus": "AUTHORIZED",
            "entitlementAvailability": "AVAILABLE",
            "regionAvailability": "AVAILABLE",
        }
        self.form = PRIVATE.encode()
        self.error = None

    def get_foundation_model_availability(self, **kwargs):
        return self.availability

    def get_use_case_for_model_access(self):
        if self.error:
            raise ClientError({"Error": {"Code": self.error, "Message": PRIVATE}}, "GetUseCase")
        return {"formData": self.form}


def test_ready_access_exposes_only_statuses_never_form_or_provider_messages():
    report = inspect_model_access(smoke_inputs()[-1].chat, client=Access())
    assert report["status"] == "passed" and report["use_case_form_present"]
    assert PRIVATE not in json.dumps(report)


@pytest.mark.parametrize("error", ["ResourceNotFoundException", "AccessDeniedException"])
def test_absent_form_and_unreadable_form_both_block_with_distinct_safe_reasons(error):
    client = Access()
    client.error = error
    report = inspect_model_access(smoke_inputs()[-1].chat, client=client)
    assert report["status"] == "blocked"
    assert report["reason"] == (
        "anthropic_use_case_required"
        if error == "ResourceNotFoundException"
        else "model_access_check_failed"
    )
    assert PRIVATE not in json.dumps(report)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("agreementAvailability", {"status": "NOT_AVAILABLE"}),
        ("agreementAvailability", {"status": "PENDING"}),
        ("agreementAvailability", {"status": PRIVATE}),
        ("authorizationStatus", "NOT_AUTHORIZED"),
        ("entitlementAvailability", "NOT_AVAILABLE"),
        ("regionAvailability", "NOT_AVAILABLE"),
        ("regionAvailability", [PRIVATE]),
    ],
)
def test_incomplete_or_malformed_access_never_authorizes_paid_smoke(field, value):
    client = Access()
    client.availability[field] = value
    report = inspect_model_access(smoke_inputs()[-1].chat, client=client)
    assert report["status"] == "blocked" and report["reason"] == "model_access_not_ready"
    assert PRIVATE not in json.dumps(report)


def test_other_providers_do_not_use_anthropic_access_api():
    assert inspect_model_access(real_settings(), client=object())["status"] == "not_required"


def test_cli_records_blocked_access_without_creating_runtime_or_reserving_inference(
    monkeypatch, tmp_path, capsys
):
    def forbidden(*args, **kwargs):
        pytest.fail("blocked access must not construct runtime or verify inference profile")

    monkeypatch.setattr("retailops_ai.adapters.bedrock_chat.BedrockChatProvider", forbidden)
    monkeypatch.setattr(
        "retailops_ai.adapters.bedrock_access.inspect_model_access",
        lambda *args, **kwargs: {"status": "blocked", "reason": "anthropic_use_case_required"},
    )
    path = tmp_path / "blocked.json"
    assert main(smoke_args() + ["--execute", "--max-cost-usd", "0.15", "--output", str(path)]) == 1
    report = json.loads(path.read_text())
    assert report["status"] == "blocked"
    assert report["reason"] == "anthropic_use_case_required"
    assert report["offline_gates_passed"] and report["aws_executed"]
    assert not report["real_chat"] and not report["ai12_closed"]
    assert report["inference_requests"] == report["count_requests"] == 0
    assert report["estimated_or_reserved_usd"] == "0"
    assert path.stat().st_mode & 0o777 == 0o600
    assert "anthropic_use_case_required" not in capsys.readouterr().out
