"""Rebuild all development role features and labels inside one private replay.

The internal builder requires an active replay. A stored manifest only verifies
artifact consistency; it does not grant campaign access or establish freshness.
"""

import hashlib
import os
import sqlite3
import tempfile
import zlib
from collections import Counter
from contextlib import closing
from pathlib import Path

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.label_contract import OutcomeEvidence
from retailops_ai.evaluation_campaign.labels import qualify_evidence
from retailops_ai.evaluation_campaign.partition_contract import (
    ALL_ROLES,
    PartitionMembership,
    PartitionRole,
)
from retailops_ai.evaluation_campaign.partitions import KEY_FIELDS, membership_key
from retailops_ai.evaluation_campaign.physical_contract import (
    PhysicalForecastDescriptor,
    PhysicalForecastExample,
    PhysicalForecastManifest,
    PhysicalForecastRecipe,
    PhysicalRoleFile,
)
from retailops_ai.evaluation_campaign.physical_versions import PhysicalVersionIndex, check_index
from retailops_ai.evaluation_campaign.source_replay import PrivateSourceReplay
from retailops_ai.forecasting.calendar import build_calendar
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifests import build_feature_set, input_models, verify_feature_set
from retailops_ai.forecasting.splits import history_index
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    inventory,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace


def _index(path: Path, maximum: int) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    path.chmod(0o600)
    db.execute("PRAGMA page_size=4096")
    db.execute("PRAGMA cache_size=-4096")
    db.execute("PRAGMA temp_store=FILE")
    db.execute(f"PRAGMA max_page_count={maximum // 4096}")
    return db


def _physical_bytes(root: Path, maximum: int) -> int:
    count = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            if (Path(base) / name).is_symlink():
                raise SnapshotError("physical_forecast_unsafe_artifact")
        for name in files:
            path = Path(base) / name
            with regular_file(root, path.relative_to(root).as_posix()) as stream:
                count += os.fstat(stream.fileno()).st_size
            if count > maximum:
                raise SnapshotError("physical_forecast_artifact_budget")
    return count


def _membership(row: InputRow, recipe: PhysicalForecastRecipe) -> PartitionMembership:
    day = row.forecast_origin.date()
    window = next((r for r in recipe.roles if r.origins.start <= day <= r.origins.end), None)
    return PartitionMembership(
        **row.model_dump(include=set(KEY_FIELDS)),
        role=window.role if window else "purged",
        label_knowledge_cutoff=window.label_knowledge_cutoff if window else None,
        feature_row_sha256=canonical_sha256(row.model_dump(mode="json")),
    )


def _counts(counter: Counter[str], example: PhysicalForecastExample) -> None:
    counter["rows"] += 1
    if example.outcome is not None:
        outcome = example.outcome
        counter["eligible"] += int(outcome.eligible)
        counter["censored"] += int(outcome.label.status == "censored")
        counter["zero"] += int(outcome.label.observed_sales_units == 0)
        counter.update({"reason:" + r: 1 for r in outcome.reasons})


def _role_file(counts: Counter[str], size: int, digest: str, keys: str) -> PhysicalRoleFile:
    return PhysicalRoleFile(
        row_count=counts["rows"],
        size_bytes=size,
        sha256=digest,
        keys_sha256=keys,
        eligible_rows=counts["eligible"],
        censored_rows=counts["censored"],
        zero_label_rows=counts["zero"],
        eligibility_reasons={
            k.removeprefix("reason:"): v for k, v in counts.items() if k.startswith("reason:") and v
        },
    )


def _write_roles(
    db: sqlite3.Connection, staging: Path, recipe: PhysicalForecastRecipe
) -> dict[PartitionRole, PhysicalRoleFile]:
    result: dict[PartitionRole, PhysicalRoleFile] = {}
    total = _physical_bytes(staging, recipe.max_artifact_bytes)
    for role in ALL_ROLES:
        digest, keys, size = hashlib.sha256(), hashlib.sha256(), 0
        counts: Counter[str] = Counter()
        with (staging / (role + ".jsonl")).open("xb") as stream:
            os.chmod(stream.name, 0o600)
            for key, body in db.execute(
                "SELECT key,body FROM examples WHERE role=? ORDER BY key", (role,)
            ):
                raw = zlib.decompress(body)
                example = PhysicalForecastExample.model_validate_json(raw)
                if membership_key(example.membership) != key or example.membership.role != role:
                    raise SnapshotError("physical_forecast_private_key_mismatch")
                line = raw + b"\n"
                size += len(line)
                total += len(line)
                if len(line) > recipe.max_record_bytes or total > recipe.max_artifact_bytes:
                    raise SnapshotError("physical_forecast_artifact_budget")
                stream.write(line)
                digest.update(line)
                keys.update(key + b"\n")
                _counts(counts, example)
        result[role] = _role_file(counts, size, digest.hexdigest(), keys.hexdigest())
    return result


def _build_physical_forecast(
    replay: PrivateSourceReplay, recipe: PhysicalForecastRecipe, output_root: Path
) -> Path:
    """No source is opened here unless the owning audited replay is still active."""
    recipe = PhysicalForecastRecipe.model_validate_json(
        canonical_bytes(recipe.model_dump(mode="json"))
    )
    replay.check_parents()
    if replay.specification_sha256 != canonical_sha256(recipe.source.model_dump(mode="json")):
        raise SnapshotError("physical_forecast_source_specification_mismatch")
    output_root = output_root.absolute()
    if output_root.is_relative_to(replay.curated.parent) or replay.curated.parent.is_relative_to(
        output_root
    ):
        raise SnapshotError("physical_forecast_distinct_output_required")
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked_directory(output_root)
    calendar = build_calendar(replay.curated, recipe.origins)
    if calendar.descriptor.parent != recipe.source.parent:
        raise SnapshotError("physical_forecast_calendar_parent_mismatch")
    with tempfile.TemporaryDirectory(prefix=".physical-forecast-", dir=output_root) as temporary:
        staging = Path(temporary)
        features = build_feature_set(
            replay.curated, calendar, staging / "feature-build", recipe.features
        )
        features.rename(staging / "features")
        (staging / "feature-build").rmdir()
        feature = verify_feature_set(staging / "features")
        if feature.descriptor.row_count > recipe.max_population_rows:
            raise SnapshotError("physical_forecast_population_budget")
        with tempfile.TemporaryDirectory(prefix="ai09-physical-index-") as scratch:
            with closing(_index(Path(scratch) / "data.sqlite", recipe.max_index_bytes)) as db:
                versions = PhysicalVersionIndex(
                    db,
                    replay.curated,
                    replay.manifest,
                    maximum_rows=recipe.source.max_rows_per_parent,
                    maximum_bytes=recipe.max_index_bytes,
                )
                try:
                    get_history = history_index(db, staging / "features")
                    check_index(db, recipe.max_index_bytes)
                    db.execute("CREATE TABLE examples(key BLOB PRIMARY KEY, role TEXT, body BLOB)")
                    db.execute("CREATE INDEX examples_by_role ON examples(role,key)")
                    count = 0
                    for row in input_models(staging / "features", "features"):
                        if not isinstance(row, InputRow):
                            raise SnapshotError("physical_forecast_feature_schema")
                        membership = _membership(row, recipe)
                        outcome = None
                        if membership.label_knowledge_cutoff is not None:
                            evidence = OutcomeEvidence(
                                key=ForecastKey.model_validate(
                                    row.model_dump(include=set(KEY_FIELDS))
                                ),
                                candidates=versions.candidates(
                                    row, membership.label_knowledge_cutoff
                                ),
                            )
                            outcome = qualify_evidence(
                                evidence,
                                membership,
                                row,
                                get_history(row.history_context_sha256),
                                recipe.features,
                                recipe.label_delay_days,
                            )
                        example = PhysicalForecastExample(membership=membership, outcome=outcome)
                        raw = canonical_bytes(example.model_dump(mode="json"))
                        if len(raw) + 1 > recipe.max_record_bytes:
                            raise SnapshotError("physical_forecast_record_budget")
                        db.execute(
                            "INSERT INTO examples VALUES (?,?,?)",
                            (membership_key(row), membership.role, zlib.compress(raw, level=1)),
                        )
                        count += 1
                        if count % 256 == 0:
                            check_index(db, recipe.max_index_bytes)
                    if count != feature.descriptor.row_count:
                        raise SnapshotError("physical_forecast_complete_population_mismatch")
                    check_index(db, recipe.max_index_bytes)
                    populations = _write_roles(db, staging, recipe)
                    descriptor = PhysicalForecastDescriptor(
                        recipe=recipe,
                        runtime=replay.runtime,
                        feature_set_id=feature.feature_set_id,
                        feature_descriptor=feature.descriptor,
                        populations=populations,
                        snapshot_inventory_sha256=replay.snapshot_inventory_sha256,
                        curated_inventory_sha256=replay.curated_inventory_sha256,
                        logical_curated_sha256=replay.logical_curated_sha256,
                        observation_rows=versions.observation_rows,
                        version_rows=versions.version_rows,
                        version_inventory_sha256=versions.version_inventory_sha256,
                    )
                finally:
                    versions.clear_cache()
        manifest = PhysicalForecastManifest(
            dataset_id="ai09-physical-forecast-sha256-"
            + canonical_sha256(descriptor.model_dump(mode="json")),
            descriptor=descriptor,
        )
        (staging / "manifest.json").write_bytes(
            canonical_bytes(manifest.model_dump(mode="json")) + b"\n"
        )
        (staging / "manifest.json").chmod(0o600)
        verify_physical_forecast(staging)
        replay.check_parents()
        fsync_tree(staging)
        destination = output_root / manifest.dataset_id
        try:
            publish_noreplace(staging, destination)
        except FileExistsError:
            if verify_physical_forecast(destination) != manifest:
                raise SnapshotError("physical_forecast_publication_conflict") from None
        replay.check_parents()
        return destination


def verify_physical_forecast(root: Path) -> PhysicalForecastManifest:
    """Check every stored key and feature/outcome binding; no source or audit claim.

    The prospective runner must bind this exact result to its durable completed
    operation. A self-resealed arbitrary manifest is never campaign evidence.
    """
    checked_directory(root)
    raw = read_bytes(root, "manifest.json", 512 * 1024)
    manifest = PhysicalForecastManifest.model_validate_json(raw)
    if raw != canonical_bytes(manifest.model_dump(mode="json")) + b"\n":
        raise SnapshotError("physical_forecast_noncanonical_manifest")
    descriptor, recipe = manifest.descriptor, manifest.descriptor.recipe
    feature = verify_feature_set(root / "features")
    if (
        feature.feature_set_id != descriptor.feature_set_id
        or feature.descriptor != descriptor.feature_descriptor
    ):
        raise SnapshotError("physical_forecast_feature_parent_mismatch")
    names = {"manifest.json", *(r + ".jsonl" for r in ALL_ROLES)}
    names.update(
        p.relative_to(root).as_posix() for p in (root / "features").rglob("*") if p.is_file()
    )
    inventory(root, names)
    _physical_bytes(root, recipe.max_artifact_bytes)
    with tempfile.TemporaryDirectory(prefix="ai09-physical-verify-") as scratch:
        with closing(_index(Path(scratch) / "keys.sqlite", recipe.max_index_bytes)) as db:
            db.execute(
                "CREATE TABLE features(key BLOB PRIMARY KEY,body BLOB,seen INTEGER DEFAULT 0)"
            )
            count = 0
            for row in input_models(root / "features", "features"):
                if not isinstance(row, InputRow):
                    raise SnapshotError("physical_forecast_feature_schema")
                db.execute(
                    "INSERT INTO features(key,body) VALUES (?,?)",
                    (
                        membership_key(row),
                        zlib.compress(canonical_bytes(row.model_dump(mode="json")), level=1),
                    ),
                )
                count += 1
                if count % 256 == 0:
                    check_index(db, recipe.max_index_bytes)
            get_history = history_index(db, root / "features")
            check_index(db, recipe.max_index_bytes)
            for role in ALL_ROLES:
                expected = descriptor.populations[role]
                if file_hash(root, role + ".jsonl") != (expected.size_bytes, expected.sha256):
                    raise SnapshotError("physical_forecast_role_checksum")
                digest, keys, size = hashlib.sha256(), hashlib.sha256(), 0
                counter: Counter[str] = Counter()
                previous: bytes | None = None
                with regular_file(root, role + ".jsonl") as stream:
                    while line := stream.readline(recipe.max_record_bytes + 1):
                        if len(line) > recipe.max_record_bytes or not line.endswith(b"\n"):
                            raise SnapshotError("physical_forecast_record_budget")
                        example = PhysicalForecastExample.model_validate_json(line)
                        if line != canonical_bytes(example.model_dump(mode="json")) + b"\n":
                            raise SnapshotError("physical_forecast_noncanonical_example")
                        key = membership_key(example.membership)
                        if previous is not None and key <= previous:
                            raise SnapshotError("physical_forecast_key_order_or_duplicate")
                        previous = key
                        found = db.execute(
                            "SELECT body,seen FROM features WHERE key=?", (key,)
                        ).fetchone()
                        if found is None or found[1]:
                            raise SnapshotError(
                                "physical_forecast_missing_or_duplicate_feature_key"
                            )
                        row = InputRow.model_validate_json(zlib.decompress(found[0]))
                        if (
                            example.membership != _membership(row, recipe)
                            or example.membership.role != role
                        ):
                            raise SnapshotError("physical_forecast_membership_binding_mismatch")
                        if example.outcome is not None:
                            selected = example.outcome.label.selected_version
                            qualified = qualify_evidence(
                                OutcomeEvidence(
                                    key=ForecastKey.model_validate(
                                        row.model_dump(include=set(KEY_FIELDS))
                                    ),
                                    candidates=(selected,) if selected else (),
                                ),
                                example.membership,
                                row,
                                get_history(row.history_context_sha256),
                                recipe.features,
                                recipe.label_delay_days,
                            )
                            if qualified != example.outcome:
                                raise SnapshotError("physical_forecast_eligibility_mismatch")
                        db.execute("UPDATE features SET seen=1 WHERE key=?", (key,))
                        digest.update(line)
                        keys.update(key + b"\n")
                        size += len(line)
                        _counts(counter, example)
                        if counter["rows"] % 256 == 0:
                            check_index(db, recipe.max_index_bytes)
                if _role_file(counter, size, digest.hexdigest(), keys.hexdigest()) != expected:
                    raise SnapshotError("physical_forecast_coverage_mismatch")
            if (
                db.execute("SELECT 1 FROM features WHERE seen!=1 LIMIT 1").fetchone()
                or count != descriptor.feature_descriptor.row_count
            ):
                raise SnapshotError("physical_forecast_incomplete_feature_population")
    return manifest
