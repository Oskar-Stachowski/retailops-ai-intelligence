"""Memory receipts refer to this executable, without inherited pre-exec Linux peaks."""

import io
from types import SimpleNamespace

import pytest

from retailops_ai.forecast_jobs import v12_executor as executor


def linux_status(monkeypatch, raw):
    class Status:
        def open(self, _):
            return io.BytesIO(raw)

    monkeypatch.setattr(executor.sys, "platform", "linux")
    monkeypatch.setattr(executor, "Path", lambda _: Status())
    monkeypatch.setattr(
        executor.resource,
        "getrusage",
        lambda _: pytest.fail("inherited pre-exec RSS must not enter Linux receipts"),
    )


def test_linux_peak_uses_current_executable_high_water_mark(monkeypatch):
    linux_status(monkeypatch, b"Name:\tpython\nVmHWM:\t82345 kB\nVmRSS:\t81000 kB\n")
    assert executor.peak_rss_bytes() == 82345 * 1024


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"VmRSS: 42 kB\n",
        b"VmHWM: 0 kB\n",
        b"VmHWM: 42 MB\n",
        b"VmHWM: -1 kB\n",
        b"VmHWM: 42 kB\nVmHWM: 43 kB\n",
        b"x" * 65537,
    ],
)
def test_missing_or_invalid_linux_measurement_cannot_bypass_memory_limit(monkeypatch, raw):
    linux_status(monkeypatch, raw)
    with pytest.raises(ValueError, match="memory_measurement_unavailable"):
        executor.peak_rss_bytes()


def test_macos_keeps_native_byte_units(monkeypatch):
    monkeypatch.setattr(executor.sys, "platform", "darwin")
    monkeypatch.setattr(executor.resource, "getrusage", lambda _: SimpleNamespace(ru_maxrss=123456))
    assert executor.peak_rss_bytes() == 123456
