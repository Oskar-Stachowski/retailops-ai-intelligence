"""Sample an in-process operation's live tree, without prior executable RSS peaks.

This reports a sampled lower bound, not a continuous peak or a resource limit.
The enclosing isolated runner remains responsible for its resource ceilings.
Only the current process and its descendants are observed; none are changed.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event, Thread
from time import perf_counter

import psutil  # type: ignore[import-untyped]

from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_journal import OperationCompletion
from retailops_ai.source_snapshot.files import SnapshotError

SAMPLE_SECONDS = 0.05


def current_tree_rss_bytes() -> int:
    parent = psutil.Process()
    total: int = parent.memory_info().rss
    if type(total) is not int or total <= 0:
        raise ValueError("current_process_memory_unavailable")
    seen = {parent.pid}
    for child in parent.children(recursive=True):
        if child.pid in seen:
            continue
        seen.add(child.pid)
        try:
            total += child.memory_info().rss
        except psutil.NoSuchProcess:
            # A child that has already exited is outside this instantaneous sample.
            continue
    return total


class SampledProcessTreeMemory:
    def __init__(self) -> None:
        self.peak: int | None = None
        self.samples = 0
        self.failed = False
        self._stop = Event()
        self._thread: Thread | None = None

    def _sample(self) -> None:
        try:
            value = current_tree_rss_bytes()
            if type(value) is not int or value <= 0:
                raise ValueError("invalid_current_tree_memory")
            self.peak = max(self.peak or 0, value)
            self.samples += 1
        except (psutil.Error, OSError, ValueError):
            self.failed = True

    def require_measurement(self) -> None:
        if self.failed or self.peak is None or not self.samples:
            raise SnapshotError("campaign_inprocess_memory_measurement_unavailable")

    def _run(self) -> None:
        while not self._stop.wait(SAMPLE_SECONDS):
            self._sample()
            if self.failed:
                return

    def start(self) -> None:
        self._sample()
        self.require_measurement()
        self._thread = Thread(target=self._run, name="ai09-inprocess-rss", daemon=True)
        try:
            self._thread.start()
        except (RuntimeError, OSError):
            self.failed = True
            raise SnapshotError("campaign_inprocess_memory_monitor_unavailable") from None

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.ident is not None:
            self._thread.join(timeout=1)
            if self._thread.is_alive():
                self.failed = True
                return
        self._sample()


@contextmanager
def measured_inprocess_operation(
    handle: OperationCompletion, started: float
) -> Iterator[SampledProcessTreeMemory]:
    """Finish measurement before the enclosing journal publishes completion.

    A failed measurement blocks success, preserves an original work exception,
    and retains unknown RSS rather than reporting zero or a partial peak.
    """
    memory = SampledProcessTreeMemory()
    interrupted = True
    try:
        memory.start()
        yield memory
        interrupted = False
    finally:
        memory.stop()
        previous = handle.cost
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=None if memory.failed else memory.peak,
            artifact_bytes=previous.artifact_bytes if previous else None,
        )
        if not interrupted:
            memory.require_measurement()
