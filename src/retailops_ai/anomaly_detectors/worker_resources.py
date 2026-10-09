"""Preserve the anomaly error contract around the shared executable memory probe."""

from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.worker_resources import worker_peak_rss_bytes as executable_peak_rss_bytes


def worker_peak_rss_bytes() -> int:
    try:
        return executable_peak_rss_bytes()
    except (OSError, ValueError) as error:
        raise SnapshotError("anomaly_worker_peak_memory_unavailable") from error
