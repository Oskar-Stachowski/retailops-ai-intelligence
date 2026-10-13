"""Verify the immutable Source-owned suggestion contract and current wire compatibility."""

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from retailops_ai.intelligence_events.suggestion_contracts import RecommendationGenerated

ROOT = Path(__file__).resolve().parents[1]
PINNED_FILES = {
    "recommendation_generated.fixture.json": "242504af2ad28150796db3870739529938cfc0921c923fd0719e829428e7bac6",
    "recommendation_generated.schema.json": "7187f64c637cb77eb3944d28776b1d0517925e676c9e63a2527eada0401d337a",
    "registry.json": "91663e01261b355452b90b0f406aaceca5dc4e261b032e7488d4dbe60171bc41",
    "suggestion-candidate.v1.schema.json": "401b3bb30cf959fc7b8f3b1cb89d60076825d31563bc4a04a464ba199eac638d",
    "suggestion.v1.schema.json": "5d7ebc50237dc98c28cebb9b93f498f5d9c26d1f46dac189bbd3392376fa2eec",
}


def main() -> int:
    folder = ROOT / "contracts/events/suggestion-source-v1"
    pin = json.loads((folder / "upstream.json").read_bytes())
    if (
        pin["commit"] != "090c26307fd508899ad6ef24504c151875db820b"
        or pin["files"] != PINNED_FILES
        or pin["repository"] != "Oskar-Stachowski/retailops-cloud-native-platform"
        or pin["qualification"] != "source_fixture_transport_contract_only"
        or pin["native_v2_transport_accepted"]
        or pin["accepted_payload_policy"] != "read-only-review-v1"
    ):
        raise ValueError("suggestion_source_owner_mismatch")
    for name, expected in pin["files"].items():
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != expected:
            raise ValueError("suggestion_source_owner_file_changed")
    schema = json.loads((folder / "recommendation_generated.schema.json").read_bytes())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    fixture = RecommendationGenerated.model_validate_json(
        (folder / "recommendation_generated.fixture.json").read_bytes()
    )
    validator.validate(fixture.model_dump(mode="json"))
    own = json.loads(
        (ROOT / "contracts/events/v2/recommendation_generated.schema.json").read_bytes()
    )
    Draft202012Validator(own, format_checker=FormatChecker()).validate(
        fixture.model_dump(mode="json")
    )
    print("Pinned Source v1 suggestion contract and emitter fixture compatibility passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
