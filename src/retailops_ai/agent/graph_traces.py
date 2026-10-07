"""Explicit bounded in-memory trace store; no raw question/results or persistent checkpoints."""

from collections.abc import Callable
from threading import Lock
from time import monotonic

from retailops_ai.agent.graph_contracts import GraphPolicy, SafeTrace
from retailops_ai.domain.access import Principal


class TraceUnavailable(ValueError):
    def __init__(self) -> None:
        super().__init__("Safe trace is unavailable.")


class MemoryTraces:
    def __init__(self, policy: GraphPolicy, *, timer: Callable[[], float] = monotonic) -> None:
        self.policy = GraphPolicy.model_validate_json(policy.model_dump_json())
        self.timer = timer
        self._entries: dict[str, tuple[float, str]] = {}
        self._lock = Lock()

    def _expire(self) -> None:
        now = self.timer()
        self._entries = {key: entry for key, entry in self._entries.items() if entry[0] > now}

    def save(self, trace: SafeTrace) -> None:
        raw = SafeTrace.model_validate_json(trace.model_dump_json()).model_dump_json()
        with self._lock:
            self._expire()
            if trace.trace_id in self._entries or len(self._entries) >= self.policy.trace_capacity:
                raise TraceUnavailable()
            self._entries[trace.trace_id] = (
                self.timer() + self.policy.trace_retention_seconds,
                raw,
            )

    def get(self, trace_id: str, principal: Principal) -> SafeTrace:
        with self._lock:
            self._expire()
            entry = self._entries.get(trace_id)
            if entry is None:
                raise TraceUnavailable()
            trace = SafeTrace.model_validate_json(entry[1])
        if (
            trace.owner_id != principal.principal_id
            or "operator" not in principal.roles
            or "assistant:query" not in principal.capabilities
            or not set(trace.scope.product_ids) <= principal.product_ids
            or not set(trace.scope.selling_location_ids) <= principal.selling_location_ids
            or trace.scope.channel not in principal.channels
        ):
            raise TraceUnavailable()
        return trace
