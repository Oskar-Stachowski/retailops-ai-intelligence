"""Small explicit acceptance fixture; these values are not source business data."""

import json
from datetime import UTC, datetime, timedelta

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.contracts import BatchRequest, MechanicsProfile


def fixture() -> tuple[MechanicsProfile, BatchRequest]:
    origin = (datetime.now(UTC) - timedelta(days=1)).replace(
        hour=23, minute=59, second=59, microsecond=0
    )
    raw = {
        "source_dataset_id": "source-sha256-" + "1" * 64,
        "curated_dataset_id": "curated-sha256-" + "2" * 64,
        "feature_set_id": "features-sha256-" + "3" * 64,
        "label_dataset_id": None,
        "split_id": None,
        "as_of_time": origin.isoformat().replace("+00:00", "Z"),
        "environment": "test",
        "purpose": "lifecycle_mechanics_only",
        "rows": [
            {
                "product_id": "p-101",
                "selling_location_id": "s-03",
                "channel": "store",
                "forecast_origin": origin.isoformat().replace("+00:00", "Z"),
                "business_timezone": "UTC",
                "cutoff_policy": "end_of_day_second_v1",
                "target_date": (origin.date() + timedelta(days=h)).isoformat(),
                "horizon_days": h,
                "value": float(h),
            }
            for h in range(1, 15)
        ],
    }
    raw["profile_id"] = "batch-profile-sha256-" + canonical_sha256(raw)
    profile = MechanicsProfile.model_validate_json(json.dumps(raw))
    return profile, BatchRequest(profile_id=profile.profile_id, as_of=origin, channel="store")
