"""Bounded shared-key guard for future baseline/RF/HGB/TensorFlow comparisons."""

import hashlib
from collections.abc import Iterable

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.source_snapshot.files import SnapshotError

MAX_COMPARISON_KEYS = 100_000


def forecast_key_bytes(key: ForecastKey) -> bytes:
    return canonical_bytes(key.model_dump(mode="json"))


def _keys(values: Iterable[ForecastKey]) -> set[bytes]:
    result: set[bytes] = set()
    for index, key in enumerate(values):
        if index >= MAX_COMPARISON_KEYS:
            raise SnapshotError("evaluation_comparison_key_limit")
        body = forecast_key_bytes(key)
        if body in result:
            raise SnapshotError("evaluation_duplicate_prediction_key")
        result.add(body)
    if not result:
        raise SnapshotError("evaluation_empty_prediction_population")
    return result


def require_same_forecast_keys(
    reference: Iterable[ForecastKey], candidate: Iterable[ForecastKey]
) -> tuple[int, str]:
    """A model-specific missing window cannot silently shrink the evaluated population."""
    expected = _keys(reference)
    if _keys(candidate) != expected:
        raise SnapshotError("evaluation_prediction_population_mismatch")
    digest = hashlib.sha256()
    for key in sorted(expected):
        digest.update(key + b"\n")
    return len(expected), digest.hexdigest()
