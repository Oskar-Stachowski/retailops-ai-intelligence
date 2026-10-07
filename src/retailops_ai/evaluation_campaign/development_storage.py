"""Reuse verified forecast I/O; share immutable lineage within one full origin only."""

import sqlite3
import tempfile
import zlib
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from retailops_ai.forecasting.features_contract import (
    HistoryContext,
    InputRow,
    InputValue,
    Reference,
)
from retailops_ai.forecasting.functional_campaign import index_parents, samples
from retailops_ai.forecasting.functional_contract import FunctionalPolicy
from retailops_ai.forecasting.manifest_contract import (
    FeatureManifest,
    FoldPlan,
    LabelPoint,
    Membership,
    SplitManifest,
)
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.dataset import Window, windows


def _value_key(value: InputValue) -> tuple[InputValue, str | None]:
    # The strict feature contract fixes numeric type for each name. Model equality
    # checks all fields; the float representation also distinguishes +0.0/-0.0.
    return value, value.value.hex() if isinstance(value.value, float) else None


def shared_records(
    db: sqlite3.Connection, records: Iterable[tuple[InputRow, Membership, LabelPoint]]
) -> Iterator[tuple[InputRow, Membership, LabelPoint, HistoryContext]]:
    """Only already validated frozen objects can share storage; all fields participate in equality.

    The cache is discarded on every product/location/channel/origin transition. It never selects
    a latest record, drops lineage, strips references or changes the serialized input contract.
    Hash collisions are resolved by full model equality plus the float representation.
    """
    previous: tuple[object, ...] | None = None
    history: HistoryContext | None = None
    references: dict[Reference, Reference] = {}
    values: dict[tuple[InputValue, str | None], InputValue] = {}
    for row, member, label in records:
        grain = (row.product_id, row.selling_location_id, row.channel, row.forecast_origin)
        if grain != previous:
            references.clear()
            values.clear()
            found = db.execute(
                "SELECT body FROM history WHERE key=?", (row.history_context_sha256,)
            ).fetchone()
            if found is None:
                raise SnapshotError("tensorflow_missing_history_parent")
            history = HistoryContext.model_validate_json(zlib.decompress(found[0]))
            previous = grain
        if history is None:
            raise SnapshotError("tensorflow_missing_history_parent")
        shared = []
        for value in row.values:
            candidate = value.model_copy(
                update={"references": tuple(references.setdefault(r, r) for r in value.references)}
            )
            shared.append(values.setdefault(_value_key(candidate), candidate))
        # This is identity sharing of fully validated immutable fields, not a projection.
        yield row.model_copy(update={"values": tuple(shared)}), member, label, history


@contextmanager
def development_parents(
    features: Path, split: Path, fold_name: str, max_windows: int = 3000
) -> Iterator[
    tuple[FeatureManifest, SplitManifest, FoldPlan, tuple[Window, ...], tuple[Window, ...]]
]:
    """Preserve the existing full parent verification, role guards and population budget."""
    with tempfile.TemporaryDirectory(prefix="ai09-shared-development-index-") as temporary:
        with sqlite3.connect(Path(temporary) / "index.sqlite") as db:
            feature, manifest, _ = index_parents(db, features, split, FunctionalPolicy())
            folds = [f for f in manifest.descriptor.resolved_policy.folds if f.name == fold_name]
            if len(folds) != 1:
                raise SnapshotError("tensorflow_unknown_development_fold")
            fold = folds[0]

            def read(role: str) -> tuple[Window, ...]:
                return windows(
                    shared_records(db, samples(db, fold, role)),
                    fold=fold,
                    role=role,
                    max_windows=max_windows,
                )

            yield feature, manifest, fold, read("train"), read("validation")
