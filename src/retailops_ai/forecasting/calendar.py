"""Verified curated binding, immutable calendar publication and fixed-origin readers."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.contract import (
    CalendarDescriptor,
    CalendarManifest,
    Implementation,
    Origin,
    OriginWindow,
    Parent,
    TaskConfig,
    make_origin,
)
from retailops_ai.source_snapshot.files import SnapshotError, decode_json, read_bytes


def default_task() -> TaskConfig:
    raw = files("retailops_ai.forecasting").joinpath("task.default.json").read_bytes()
    return TaskConfig.model_validate_json(raw)


def implementation() -> Implementation:
    roots = {
        "forecasting": files("retailops_ai.forecasting"),
        "data_contracts": files("retailops_ai.data_contracts"),
        "curated": files("retailops_ai.curated"),
    }
    names = {
        "forecasting": ("contract.py", "calendar.py"),
        "data_contracts": ("common.py", "identity.py"),
        "curated": ("reader.py",),
    }
    hashes = {
        f"{package}/{name}": hashlib.sha256(roots[package].joinpath(name).read_bytes()).hexdigest()
        for package, package_names in names.items()
        for name in package_names
    }
    return Implementation(
        version="forecast-calendar-1.0.0", code_files=hashes, code_sha256=canonical_sha256(hashes)
    )


def verified_parent(curated_dir: Path) -> Parent:
    from retailops_ai.curated.builder import verify_curated

    document = verify_curated(curated_dir)
    descriptor = document["descriptor"]
    if document["readiness"]["forecast_source"] != "passed":
        raise SnapshotError("forecast_source_not_ready")
    return Parent(
        source_dataset_id=descriptor["parent_source_dataset_id"],
        curated_dataset_id=document["curated_dataset_id"],
        snapshot_id=descriptor["parent_snapshot_id"],
        curated_descriptor_sha256=canonical_sha256(descriptor),
        business_timezone=descriptor["config"]["business_timezone"],
        forecast_source_status="passed",
    )


def build_calendar(
    curated_dir: Path,
    window: OriginWindow,
    *,
    task: TaskConfig | None = None,
    generated_at: datetime | None = None,
) -> CalendarManifest:
    resolved = task if task is not None else default_task()
    origins = tuple(
        make_origin(window.start + timedelta(days=i))
        for i in range((window.end - window.start).days + 1)
    )
    descriptor = CalendarDescriptor(
        schema_version="1.0.0",
        task=resolved,
        task_id=resolved.task_id(),
        parent=verified_parent(curated_dir),
        origin_window=window,
        implementation=implementation(),
        calendar_content_sha256=canonical_sha256([o.model_dump(mode="json") for o in origins]),
    )
    return CalendarManifest(
        schema_version="1.0.0",
        calendar_id="forecast-calendar-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
        origins=origins,
        forecast_model_status="not_ready",
        generated_at=generated_at if generated_at is not None else datetime.now(UTC),
    )


def load_calendar(path: Path) -> CalendarManifest:
    raw = read_bytes(path.absolute().parent, path.name)
    # Also reject duplicate JSON keys and non-finite numbers, rather than accepting last key wins.
    decode_json(raw)
    return CalendarManifest.model_validate_json(raw)


def publish_calendar(manifest: CalendarManifest, root: Path) -> Path:
    """One exclusive file per content ID; repeated publication retains original runtime metadata."""
    # Frozen models can still contain mutable dictionaries; check their current contents again.
    manifest = CalendarManifest.model_validate_json(manifest.model_dump_json())
    root.mkdir(parents=True, exist_ok=True)
    destination = root / (manifest.calendar_id + ".json")
    raw = manifest.model_dump_json(indent=2).encode() + b"\n"
    # Prepare complete bytes privately, then link atomically without overwriting an existing ID.
    import os
    import tempfile

    descriptor, temporary = tempfile.mkstemp(prefix=".forecast-calendar-", dir=root)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except FileExistsError:
            existing = load_calendar(destination)
            if existing.model_dump(exclude={"generated_at"}) != manifest.model_dump(
                exclude={"generated_at"}
            ):
                raise SnapshotError("forecast_calendar_publication_conflict") from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def rows_for_origin(
    curated_dir: Path,
    manifest: CalendarManifest,
    origin_date: date,
    *,
    table: str = "daily_demand_versions",
    target_date: date | None = None,
) -> Iterator[dict[str, Any]]:
    """History or known plans share the SAME cutoff throughout all 14 target days."""
    from retailops_ai.curated.reader import PLAN_KEYS, rows_as_of

    if verified_parent(curated_dir) != manifest.descriptor.parent:
        raise SnapshotError("forecast_calendar_parent_mismatch")
    origin: Origin | None = next(
        (o for o in manifest.origins if o.origin_date == origin_date), None
    )
    if origin is None:
        raise SnapshotError("forecast_origin_outside_calendar")
    if table == "daily_demand_versions":
        if target_date is not None:
            raise SnapshotError("forecast_history_does_not_advance_to_target")
    elif table not in PLAN_KEYS or target_date not in {t.target_date for t in origin.targets}:
        raise SnapshotError("forecast_requires_supported_plan_and_calendar_target")
    yield from rows_as_of(
        curated_dir, origin.availability_cutoff, table=table, business_date=target_date
    )
