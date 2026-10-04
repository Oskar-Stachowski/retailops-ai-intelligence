"""Read only training/development samples using the existing verified disk index."""

import sqlite3
import tempfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
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


@contextmanager
def development_parents(
    features: Path, split: Path, fold_name: str, max_windows: int = 3000
) -> Iterator[
    tuple[FeatureManifest, SplitManifest, FoldPlan, tuple[Window, ...], tuple[Window, ...]]
]:
    with tempfile.TemporaryDirectory(prefix="ai09-development-index-") as temporary:
        with sqlite3.connect(Path(temporary) / "index.sqlite") as db:
            feature, manifest, _ = index_parents(db, features, split, FunctionalPolicy())
            folds = [f for f in manifest.descriptor.resolved_policy.folds if f.name == fold_name]
            if len(folds) != 1:
                raise SnapshotError("tensorflow_unknown_development_fold")
            fold = folds[0]

            def read(role: str) -> tuple[Window, ...]:
                def records() -> Iterator[tuple[InputRow, Membership, LabelPoint, HistoryContext]]:
                    for row, member, label in samples(db, fold, role):
                        history = db.execute(
                            "SELECT body FROM history WHERE key=?", (row.history_context_sha256,)
                        ).fetchone()
                        if history is None:
                            raise SnapshotError("tensorflow_missing_history_parent")
                        yield (
                            row,
                            member,
                            label,
                            HistoryContext.model_validate_json(zlib.decompress(history[0])),
                        )

                return windows(records(), fold=fold, role=role, max_windows=max_windows)

            yield feature, manifest, fold, read("train"), read("validation")
