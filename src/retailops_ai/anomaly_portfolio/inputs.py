"""Native verification creates a sealed in-memory training input without truth."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from retailops_ai.full_raw_dq.store import Manifest as ReplayManifest
from retailops_ai.qualified_anomalies.contract import Point
from retailops_ai.qualified_anomalies.store import Manifest, verify
from retailops_ai.source_snapshot.files import json_sha256, read_bytes


@dataclass(frozen=True)
class VerifiedFeatures:
    directory: Path
    manifest: Manifest
    manifest_sha256: str
    replay_manifest: ReplayManifest

    def points(self) -> tuple[Point, ...]:
        raw = read_bytes(self.directory, "features.jsonl", 128 * 1024**2)
        document = read_bytes(self.directory, "feature_manifest.json", 1024**2)
        if (
            hashlib.sha256(raw).hexdigest() != self.manifest.descriptor.rows_sha256
            or hashlib.sha256(document).hexdigest() != self.manifest_sha256
        ):
            raise ValueError("anomaly_verified_feature_changed")
        points = tuple(Point.model_validate_json(line) for line in raw.splitlines())
        if len(points) != self.manifest.descriptor.row_count:
            raise ValueError("anomaly_verified_feature_row_count")
        return points


def verified_features(
    directory: Path, replay: Path, coverage: Path, curated: Path, snapshot: Path
) -> VerifiedFeatures:
    manifest = verify(directory, replay, coverage, curated, snapshot)
    full = ReplayManifest.model_validate_json(read_bytes(replay, "dq_manifest.json", 1024**2))
    if (
        full.full_dq_replay_id != manifest.descriptor.full_dq_replay_id
        or json_sha256(full.descriptor.model_dump(mode="json"))
        != manifest.descriptor.full_dq_descriptor_sha256
    ):
        raise ValueError("anomaly_verified_parent_changed")
    result = VerifiedFeatures(
        directory,
        manifest,
        hashlib.sha256(read_bytes(directory, "feature_manifest.json", 1024**2)).hexdigest(),
        full,
    )
    result.points()
    return result
