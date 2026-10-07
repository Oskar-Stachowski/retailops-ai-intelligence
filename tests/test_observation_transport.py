"""Transport limits and exact byte identity before any database or ACK operation."""

from dataclasses import replace

import pytest
from sqlalchemy import create_engine

from retailops_ai.source_replay.history import ReplayError
from retailops_ai.source_replay.store import ObservationStore, TransportRecord


@pytest.mark.parametrize(
    "changes",
    [
        {"partition": True},
        {"partition": -1},
        {"partition": 32},
        {"offset": True},
        {"offset": -1},
        {"offset": 2**63 - 1},
        {"value": "private"},
        {"value": b"x" * 32769},
        {"key": "private"},
        {"key": b"x" * 4097},
        {"timestamp_ms": True},
        {"timestamp_ms": -1},
        {"timestamp_ms": 2**63 - 1},
        {"headers": []},
        {"headers": (("a", b"x"),) * 17},
        {"headers": (("", b"x"),)},
        {"headers": (("a", "x"),)},
        {"headers": (("a", b"x" * 4096),)},
        {"headers": (("a",),)},
    ],
)
def test_invalid_or_unbounded_transport_stops_before_sql(changes):
    with pytest.raises(ReplayError, match="^observation_"):
        TransportRecord(**{"partition": 0, "offset": 0, "value": b"{}", **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"value": b" { } "},
        {"value": None},
        {"key": b"key"},
        {"key": b""},
        {"headers": (("a", None),)},
        {"headers": (("a", b""),)},
        {"headers": (("b", b"2"), ("a", b"1"))},
        {"timestamp_ms": 1},
    ],
)
def test_changed_raw_record_or_metadata_cannot_replay_original_receipt(changes):
    original = TransportRecord(0, 0, b"{}", headers=(("a", b"1"), ("b", b"2")))
    assert replace(original, **changes).fingerprint() != original.fingerprint()


def test_fingerprint_preserves_tombstone_empty_bytes_and_duplicate_header_order():
    assert TransportRecord(0, 0, None).fingerprint() != TransportRecord(0, 0, b"").fingerprint()
    first = TransportRecord(0, 0, b"", headers=(("a", b"1"), ("a", b"2")))
    assert (
        first.fingerprint() != replace(first, headers=tuple(reversed(first.headers))).fingerprint()
    )
    assert first.fingerprint() == replace(first).fingerprint()


def test_store_requires_actual_postgresql():
    with pytest.raises(ReplayError, match="postgresql_required"):
        ObservationStore(create_engine("sqlite://"))


def test_record_representation_does_not_expose_private_payload_key_or_headers():
    transport = TransportRecord(
        0,
        0,
        b"private-payload",
        key=b"private-key",
        headers=(("private-header", b"private-value"),),
    )
    assert "private" not in repr(transport)
