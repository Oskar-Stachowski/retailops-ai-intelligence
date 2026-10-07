"""Publisher mechanics bind canonical native output and wire ACKs without qualifying fixture models."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_model_intelligence_events import model_events as model_events

from retailops_ai.data_contracts.identity import canonical_sha256


@pytest.fixture
def subject(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/deliver_ai10_model_native.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("ai10_model_delivery_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=[0, 1])
def original(request, model_events):
    event = model_events[request.param]
    row = SimpleNamespace(
        event_id=event.event_id,
        document=event.model_dump(mode="json"),
        environment="test",
        topic=event.topic,
        partition_key=event.partition_key,
        delivered_at=None,
        delivered_partition=None,
        delivered_offset=None,
    )
    control = dict(
        rows=1,
        kind=event.event_type,
        correlation_id=str(event.correlation_id),
        census_sha256=canonical_sha256(
            {
                str(event.event_id): canonical_sha256(event.model_dump(mode="json")),
            }
        ),
    )
    return row, control


def test_complete_pending_and_acknowledged_census_preserve_both_serializations(subject, original):
    row, control = original
    pending = subject.census([row], control, pending=True)
    # The real publisher uses typed JSON; the sealed export uses canonical JSON.
    # Their distinct hashes must remain explicit while payload identity stays bound.
    assert len(pending) == 1
    assert next(iter(pending.values()))[0] != next(iter(pending.values()))[1]
    row.delivered_at, row.delivered_partition, row.delivered_offset = datetime.now(UTC), 1, 7
    assert subject.census([row], control, pending=False) == pending


@pytest.mark.parametrize(
    "change", ["missing", "extra", "batch", "digest", "key", "partial", "unconfirmed"]
)
def test_displaced_incomplete_or_unconfirmed_original_publication_refused(
    subject, original, change
):
    row, control = original
    rows, pending = [row], True
    if change == "missing":
        rows = []
    elif change == "extra":
        rows.append(row)
    elif change == "batch":
        control["correlation_id"] = "foreign-batch"
    elif change == "digest":
        control["census_sha256"] = "f" * 64
    elif change == "key":
        row.partition_key = "foreign-grain"
    elif change == "partial":
        row.delivered_offset = 7
    else:
        pending = False
    with pytest.raises(ValueError):
        subject.census(rows, control, pending=pending)
