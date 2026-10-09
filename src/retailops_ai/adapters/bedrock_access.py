"""Read-only account prerequisites for the bounded Anthropic smoke; never enroll."""

from typing import Protocol, cast

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from retailops_ai.agent.chat_config import ResolvedChatConfig


class AccessClient(Protocol):
    def get_use_case_for_model_access(self) -> dict[str, object]: ...
    def get_foundation_model_availability(self, **kwargs: object) -> dict[str, object]: ...


def inspect_model_access(
    config: ResolvedChatConfig,
    profile: str | None = None,
    *,
    client: AccessClient | None = None,
) -> dict[str, object]:
    """Only bounded status values leave this check, never form data or AWS error text."""
    model = config.verified().config.model
    if not model.model_id.startswith("anthropic."):
        return {"status": "not_required", "reason": None}
    report: dict[str, object] = {
        "status": "blocked",
        "reason": "model_access_check_failed",
        "use_case_form_present": None,
        "availability": {},
    }
    try:
        if client is None:
            import boto3  # type: ignore[import-untyped]
            from botocore.config import Config  # type: ignore[import-untyped]

            client = cast(
                AccessClient,
                boto3.Session(profile_name=profile).client(
                    "bedrock",
                    region_name=model.region,
                    config=Config(
                        connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1}
                    ),
                ),
            )
        response = client.get_foundation_model_availability(modelId=model.model_id)
        agreement = response.get("agreementAvailability")
        values = {
            "agreement": agreement.get("status") if isinstance(agreement, dict) else None,
            "authorization": response.get("authorizationStatus"),
            "entitlement": response.get("entitlementAvailability"),
            "region": response.get("regionAvailability"),
        }
        allowed = {
            "agreement": {"AVAILABLE", "NOT_AVAILABLE", "PENDING", "ERROR"},
            "authorization": {"AUTHORIZED", "NOT_AUTHORIZED"},
            "entitlement": {"AVAILABLE", "NOT_AVAILABLE"},
            "region": {"AVAILABLE", "NOT_AVAILABLE"},
        }
        report["availability"] = {
            name: value if isinstance(value, str) and value in allowed[name] else "UNKNOWN"
            for name, value in values.items()
        }
        try:
            form = client.get_use_case_for_model_access().get("formData")
            report["use_case_form_present"] = isinstance(form, (str, bytes)) and bool(form)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                raise
            report["use_case_form_present"] = False
        if not report["use_case_form_present"]:
            report["reason"] = "anthropic_use_case_required"
        elif report["availability"] != {
            "agreement": "AVAILABLE",
            "authorization": "AUTHORIZED",
            "entitlement": "AVAILABLE",
            "region": "AVAILABLE",
        }:
            report["reason"] = "model_access_not_ready"
        else:
            report.update(status="passed", reason=None)
    except (ClientError, BotoCoreError, ValueError, TypeError, AttributeError):
        # Unknown access is not permission to start paid inference.
        pass
    return report
