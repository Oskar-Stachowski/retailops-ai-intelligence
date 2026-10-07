"""Sharing keeps the complete input and never crosses the full forecast origin."""

import sqlite3
import zlib
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import timeline as timeline
from test_tensorflow_challenger import FEATURE_ID, SPLIT_ID
from test_tensorflow_challenger import development as development

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.development_storage import (
    development_parents,
    shared_records,
)
from retailops_ai.forecasting.features_contract import InputRow, Reference
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.dataset import fit_normalization, population_sha256
from retailops_ai.tensorflow_challenger.parents import development_parents as legacy_parents


def indexed(db, windows):
    db.execute("CREATE TABLE history(key TEXT PRIMARY KEY, body BLOB)")
    for window in windows:
        db.execute(
            "INSERT INTO history VALUES (?,?)",
            (
                window.history.content_sha256(),
                zlib.compress(canonical_bytes(window.history.model_dump(mode="json"))),
            ),
        )


def records(windows):
    for window in windows:
        for sample in window.samples:
            # A decoded row has independent lineage objects, just like the production reader.
            yield (
                InputRow.model_validate_json(sample.row.model_dump_json()),
                sample.membership,
                sample.label,
            )


def test_all_fields_preserved_and_equal_lineage_actually_shares_storage(development):
    _, train, validation = development
    originals = list(records((*train, *validation)))
    with sqlite3.connect(":memory:") as db:
        indexed(db, (*train, *validation))
        shared = list(shared_records(db, originals))
    assert [r.model_dump(mode="json") for r, _, _, _ in shared] == [
        r.model_dump(mode="json") for r, _, _ in originals
    ]
    assert [(m, label) for _, m, label, _ in shared] == [(m, label) for _, m, label in originals]
    first, second = shared[0][0], shared[1][0]
    assert first.values[0] is second.values[0]
    assert shared[0][3] is shared[1][3]
    original_ids = {id(ref) for row, _, _ in originals for v in row.values for ref in v.references}
    shared_ids = {id(ref) for row, _, _, _ in shared for v in row.values for ref in v.references}
    assert len(shared_ids) < len(original_ids)
    with pytest.raises(ValidationError, match="frozen_instance"):
        first.values[0].value = 100


@pytest.mark.parametrize("field", ["record_sha256", "available_at"])
def test_reference_content_and_availability_changes_are_retained(development, field, monkeypatch):
    # A collision must not turn changed historical knowledge into a shared reference.
    monkeypatch.setattr(Reference, "__hash__", lambda self: 0)
    _, train, _ = development
    original = list(records(train))
    row, member, label = original[1]
    index = next(i for i, v in enumerate(row.values) if v.references)
    value = row.values[index]
    ref = value.references[0]
    change = "f" * 64 if field == "record_sha256" else ref.available_at - timedelta(seconds=1)
    changed = ref.model_copy(update={field: change})
    value = value.model_copy(update={"references": (changed, *value.references[1:])})
    row = InputRow.model_validate_json(
        row.model_copy(
            update={"values": (*row.values[:index], value, *row.values[index + 1 :])}
        ).model_dump_json()
    )
    original[1] = row, member, label
    with sqlite3.connect(":memory:") as db:
        indexed(db, train)
        shared = list(shared_records(db, original))
    assert shared[1][0].model_dump(mode="json") == row.model_dump(mode="json")
    assert shared[0][0].values[index].references[0] is not shared[1][0].values[index].references[0]
    assert getattr(shared[1][0].values[index].references[0], field) == change


def test_cache_is_discarded_when_full_origin_changes(development):
    _, train, validation = development
    original = list(records((*train, *validation)))
    # Return to a former origin: equality still holds, but there is no global/latest cache.
    original.append(next(records(train)))
    with sqlite3.connect(":memory:") as db:
        indexed(db, (*train, *validation))
        shared = list(shared_records(db, original))
    assert shared[0][0] == shared[-1][0]
    assert shared[0][0].values[0] is not shared[-1][0].values[0]
    assert shared[0][3] == shared[-1][3] and shared[0][3] is not shared[-1][3]


@pytest.mark.parametrize("field", ["product_id", "selling_location_id", "channel"])
def test_cache_does_not_cross_a_series_boundary(development, field):
    _, train, _ = development
    window = train[0]
    first = next(records(train))
    change = "online" if field == "channel" else "different-series"
    history = window.history.model_copy(
        update={
            field: change,
            "points": tuple(p.model_copy(update={field: change}) for p in window.history.points),
        }
    )
    row = first[0].model_copy(
        update={field: change, "history_context_sha256": history.content_sha256()}
    )
    if field == "channel":
        row = row.model_copy(
            update={
                "values": tuple(
                    v.model_copy(update={"value": change}) if v.name == "channel" else v
                    for v in row.values
                )
            }
        )
    row = InputRow.model_validate_json(row.model_dump_json())
    with sqlite3.connect(":memory:") as db:
        indexed(db, train)
        db.execute(
            "INSERT INTO history VALUES (?,?)",
            (
                history.content_sha256(),
                zlib.compress(canonical_bytes(history.model_dump(mode="json"))),
            ),
        )
        shared = list(shared_records(db, (first, (row, first[1], first[2]), first)))
    assert shared[1][0].model_dump(mode="json") == row.model_dump(mode="json")
    assert shared[0][0] == shared[-1][0]
    assert shared[0][0].values[0] is not shared[-1][0].values[0]


def test_missing_history_is_rejected(development):
    _, train, _ = development
    with sqlite3.connect(":memory:") as db:
        indexed(db, ())
        with pytest.raises(SnapshotError, match="missing_history_parent"):
            next(shared_records(db, records(train)))


def test_streamed_population_digest_preserves_array_order_and_all_fields(development):
    _, train, validation = development
    for population in ((), train, validation, (*train, *validation)):
        assert population_sha256(iter(population)) == canonical_sha256(
            [s.row.model_dump(mode="json") for w in population for s in w.samples]
        )
    assert population_sha256((*train, *validation)) != population_sha256((*validation, *train))


@pytest.mark.parametrize("zeros", [(0.0, -0.0), (-0.0, 0.0)])
def test_sharing_preserves_signed_zero_wire_bytes(development, zeros):
    _, train, _ = development
    originals = list(records(train))[:2]
    index = next(i for i, v in enumerate(originals[0][0].values) if v.name == "rolling_std_7")
    for i, zero in enumerate(zeros):
        row, member, label = originals[i]
        value = row.values[index].model_copy(update={"value": zero})
        row = InputRow.model_validate_json(
            row.model_copy(
                update={"values": (*row.values[:index], value, *row.values[index + 1 :])}
            ).model_dump_json()
        )
        originals[i] = row, member, label
    with sqlite3.connect(":memory:") as db:
        indexed(db, train)
        shared = list(shared_records(db, originals))
    assert [canonical_bytes(r.model_dump(mode="json")) for r, _, _, _ in shared] == [
        canonical_bytes(r.model_dump(mode="json")) for r, _, _ in originals
    ]
    assert shared[0][0].values[index] is not shared[1][0].values[index]


def wire(windows):
    return [
        (
            w.history.model_dump(mode="json"),
            [
                (
                    s.row.model_dump(mode="json"),
                    s.membership.model_dump(mode="json"),
                    s.label.model_dump(mode="json"),
                )
                for s in w.samples
            ],
        )
        for w in windows
    ]


def test_verified_parent_adapter_matches_legacy_and_train_normalization(artifacts):
    features, split, *_ = artifacts
    with legacy_parents(features, split, "fold-a") as old:
        with development_parents(features, split, "fold-a") as new:
            assert old[:3] == new[:3]
            assert wire(old[3]) == wire(new[3]) and wire(old[4]) == wire(new[4])
            states = [
                fit_normalization(p[3], fold=p[2], feature_set_id=FEATURE_ID, split_id=SPLIT_ID)
                for p in (old, new)
            ]
            assert states[0].content_sha256() == states[1].content_sha256()
    with pytest.raises(SnapshotError, match="window_budget"):
        with development_parents(features, split, "fold-a", max_windows=0):
            pass
    with pytest.raises(SnapshotError, match="unknown_development_fold"):
        with development_parents(features, split, "absent"):
            pass
