"""Peak RSS of the worker's current executable image, without pre-exec history."""

import resource
import sys
from pathlib import Path

from retailops_ai.source_snapshot.files import SnapshotError


def worker_peak_rss_bytes() -> int:
    if sys.platform != "linux":
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value * (1 if sys.platform == "darwin" else 1024))
    # Linux getrusage retains pre-exec usage. A forked worker can therefore
    # report its parent's old RSS, which must not be added to that parent again.
    # VmHWM belongs to the current mm; the supervisor still measures the full
    # live parent/child tree, including startup, independently of this receipt.
    with Path("/proc/self/status").open("rb") as stream:
        raw = stream.read(65537)
    matches = [line.split() for line in raw.splitlines() if line.startswith(b"VmHWM:")]
    if (
        len(raw) > 65536
        or len(matches) != 1
        or len(matches[0]) != 3
        or matches[0][2] != b"kB"
        or not matches[0][1].isdigit()
        or int(matches[0][1]) <= 0
    ):
        raise SnapshotError("anomaly_census_worker_peak_memory_unavailable")
    return int(matches[0][1]) * 1024
