"""Stream the original private qualification bound to a public Source snapshot.

Internal intake only: the caller must reserve the complete private read and
verify the Source generation and snapshot first. A receipt requires complete
consumption and successful context exit. Ledger replay is a separate step.
"""

import codecs
import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, BinaryIO, Literal, Self

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]
from pydantic import Field

from retailops_ai.data_contracts.common import Contract, utc_time
from retailops_ai.evaluation_campaign.source_replay import _seal_parent
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    decode_json,
    json_sha256,
    nonfinite,
    read_bytes,
    regular_file,
    unique_keys,
)
from retailops_ai.source_snapshot.inventory_protocol import resource_bytes
from retailops_ai.source_snapshot.protocol import Snapshot

MANIFEST = "qualification_manifest.json"
WINDOWS = "simulation_truth/inventory_qualified_windows.json"
REPORT = "qualification_report.json"
NAMES = {MANIFEST, WINDOWS, REPORT}


class CampaignStockoutQualificationPolicy(Contract):
    version: Literal["ai09-stockout-full-qualification-read-1.0.0"] = (
        "ai09-stockout-full-qualification-read-1.0.0"
    )
    max_windows: Annotated[int, Field(ge=1, le=1_000_000)] = 1_000_000
    max_bytes: Annotated[int, Field(ge=4096, le=4 * 1024**3)] = 2 * 1024**3
    max_record_bytes: Annotated[int, Field(ge=1024, le=65536)] = 16384
    chunk_bytes: Annotated[int, Field(ge=1, le=65536)] = 8192


DEFAULT_QUALIFICATION_POLICY = CampaignStockoutQualificationPolicy()


def _canonical_objects(
    stream: BinaryIO, policy: CampaignStockoutQualificationPolicy
) -> Iterator[dict[str, Any]]:
    """Native canonical JSON array with bounded incremental UTF-8 decoding."""
    decoder = json.JSONDecoder(object_pairs_hook=unique_keys, parse_constant=nonfinite)
    utf8 = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    size = count = 0
    eof = False

    def fill() -> None:
        nonlocal buffer, size, eof
        raw = stream.read(policy.chunk_bytes)
        size += len(raw)
        if size > policy.max_bytes:
            raise SnapshotError("campaign_stockout_qualification_byte_budget")
        eof = not raw
        buffer += utf8.decode(raw, final=eof)

    def require_prefix(prefix: str) -> None:
        nonlocal buffer
        while len(buffer) < len(prefix) and not eof:
            fill()
        if not buffer.startswith(prefix):
            raise SnapshotError("campaign_stockout_qualification_canonical_array_required")
        buffer = buffer[len(prefix) :]

    try:
        require_prefix("[")
        while True:
            if not buffer and not eof:
                fill()
            if buffer.startswith("]"):
                break
            if count:
                require_prefix(",")
            while True:
                try:
                    value, end = decoder.raw_decode(buffer)
                    break
                except json.JSONDecodeError:
                    if eof or len(buffer.encode("utf-8")) > policy.max_record_bytes:
                        raise SnapshotError(
                            "campaign_stockout_qualification_invalid_or_oversized_record"
                        ) from None
                    fill()
            raw = buffer[:end].encode("utf-8")
            if (
                not isinstance(value, dict)
                or len(raw) > policy.max_record_bytes
                or raw != canonical_json(value)
            ):
                raise SnapshotError("campaign_stockout_qualification_noncanonical_record")
            buffer = buffer[end:]
            count += 1
            if count > policy.max_windows:
                raise SnapshotError("campaign_stockout_qualification_window_budget")
            yield value
        require_prefix("]\n")
        while not eof:
            fill()
            if buffer:
                raise SnapshotError("campaign_stockout_qualification_trailing_bytes")
        if buffer:
            raise SnapshotError("campaign_stockout_qualification_trailing_bytes")
    except (UnicodeError, RecursionError) as exc:
        raise SnapshotError("campaign_stockout_qualification_invalid_encoding_or_depth") from exc


class CampaignStockoutQualification:
    def __init__(
        self,
        root: Path,
        snapshot: Snapshot,
        *,
        allow_evaluation_truth: bool = False,
        policy: CampaignStockoutQualificationPolicy = DEFAULT_QUALIFICATION_POLICY,
    ) -> None:
        self.root, self.allow_truth = root, allow_evaluation_truth
        self.policy = CampaignStockoutQualificationPolicy.model_validate_json(
            policy.model_dump_json()
        )
        self._source = deepcopy(snapshot.manifest)
        self._used = self._active = self._started = self._exhausted = self._complete = False
        self._failed = False
        self._receipt: dict[str, Any] = {}
        self._stack = ExitStack()

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("campaign_stockout_qualification_single_use")
        self._used = True
        if self.allow_truth is not True:
            raise SnapshotError("campaign_stockout_qualification_private_opt_in_required")
        self.root = checked_directory(self.root)
        manifest = self._source
        if (
            manifest["schema_version"] not in ("1.1.0", "1.2.0")
            or manifest["descriptor"]["include_evaluation_truth"] is not False
            or manifest["snapshot_ready"] is not True
            or manifest["source"]["facts_ready"] is not True
        ):
            raise SnapshotError("campaign_stockout_qualification_verified_public_snapshot_required")
        self._seal = _seal_parent(self.root, NAMES, self.policy.max_bytes)
        raw = read_bytes(self.root, MANIFEST)
        self.manifest = decode_json(raw)
        self.descriptor = deepcopy(manifest["descriptor"]["qualification"])
        schema = decode_json(
            resource_bytes("qualification.schema.json", manifest["schema_version"])
        )
        try:
            Draft202012Validator(schema["manifest"]).validate(self.manifest)
        except ValidationError as exc:
            raise SnapshotError("campaign_stockout_qualification_invalid_manifest") from exc
        provenance = self.manifest["provenance"]
        if (
            raw != canonical_json(self.manifest) + b"\n"
            or self.manifest["descriptor"] != self.descriptor
            or self.manifest["qualification_id"]
            != manifest["descriptor"]["parent_qualification_id"]
            or self.manifest["qualification_id"]
            != "inventory-labels-sha256-" + json_sha256(self.descriptor)
            or self.descriptor["parent_source_id"] != manifest["source_dataset_id"]
            or self.descriptor["evaluated_at"]
            != manifest["source"]["descriptor"]["context"]["evaluated_at"]
            or any(
                json_sha256(provenance[kind + "_files"]) != provenance[kind + "_sha256"]
                or provenance[kind + "_sha256"] != self.descriptor[kind + "_sha256"]
                for kind in ("code", "dependency")
            )
            or provenance["python_version"] != self.descriptor["python_version"]
            or self.descriptor["qualification_schema_sha256"] != json_sha256(schema)
            or self.descriptor["rows"] > self.policy.max_windows
        ):
            raise SnapshotError("campaign_stockout_qualification_original_parent_mismatch")
        for field, name in (("windows", WINDOWS), ("report", REPORT)):
            ref = self.manifest[field]
            if ref["path"] != name or (ref["size_bytes"], ref["sha256"]) != self._seal[name]:
                raise SnapshotError("campaign_stockout_qualification_original_artifact_mismatch")
        report = read_bytes(self.root, REPORT)
        self._report = decode_json(report)
        if (
            report != canonical_json(self._report) + b"\n"
            or json_sha256(self._report) != self.descriptor["report_sha256"]
        ):
            raise SnapshotError("campaign_stockout_qualification_original_report_mismatch")
        self._validator = Draft202012Validator(schema["window"])
        self._metadata_sha256 = json_sha256(
            [self._source, self.manifest, self.descriptor, self._report]
        )
        self._active = True
        return self

    def __exit__(self, *args: Any) -> None:
        try:
            if args[0] is None:
                if self._failed or not self._exhausted:
                    raise SnapshotError("campaign_stockout_qualification_complete_stream_required")
                if self._metadata_sha256 != json_sha256(
                    [self._source, self.manifest, self.descriptor, self._report]
                ):
                    raise SnapshotError("campaign_stockout_qualification_metadata_changed")
                if _seal_parent(self.root, NAMES, self.policy.max_bytes) != self._seal:
                    raise SnapshotError("campaign_stockout_qualification_parent_changed")
                self._complete = True
        finally:
            self._active = False
            self._stack.close()

    def rows(self) -> Iterator[dict[str, Any]]:
        if not self._active or self._started or self._failed:
            self._failed = True
            raise SnapshotError("campaign_stockout_qualification_closed_or_repeated_stream")
        self._started = True
        previous = None
        count = positive = negative = 0
        statuses: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        digest = hashlib.sha256(b"[")
        try:
            with self._stack.enter_context(regular_file(self.root, WINDOWS)) as stream:
                for row in _canonical_objects(stream, self.policy):
                    if not self._active:
                        raise SnapshotError("campaign_stockout_qualification_closed_stream")
                    try:
                        self._validator.validate(row)
                    except ValidationError as exc:
                        raise SnapshotError(
                            "campaign_stockout_qualification_invalid_window"
                        ) from exc
                    origin = utc_time(datetime.fromisoformat(row["origin"]))
                    end = utc_time(datetime.fromisoformat(row["window_end_at"]))
                    evaluated = utc_time(datetime.fromisoformat(row["evaluated_at"]))
                    available = (
                        utc_time(datetime.fromisoformat(row["label_available_at"]))
                        if row["label_available_at"]
                        else None
                    )
                    key = row["product_id"], row["stock_location_id"], origin
                    if (
                        previous is not None
                        and key <= previous
                        or end != origin + timedelta(days=7)
                        or evaluated < origin
                        or row["evaluated_at"] != self.descriptor["evaluated_at"]
                        or (row["status"] == "evaluable") != (row["incident_stockout"] is not None)
                        or row["status"] == "evaluable"
                        and (available is None or not end <= available <= evaluated)
                    ):
                        raise SnapshotError(
                            "campaign_stockout_qualification_window_identity_or_chronology"
                        )
                    previous = key
                    if count:
                        digest.update(b",")
                    digest.update(canonical_json(row))
                    count += 1
                    statuses[row["status"]] += 1
                    if row["reason"] is not None:
                        reasons[row["reason"]] += 1
                    positive += int(row["incident_stockout"] == 1)
                    negative += int(row["incident_stockout"] == 0)
                    yield row
            digest.update(b"]")
            expected = dict(
                rows=count,
                parent_source_id=self._source["source_dataset_id"],
                positive_labels=positive,
                negative_labels=negative,
                statuses=dict(statuses),
                reasons=dict(reasons),
                source_facts_ready=True,
                label_qualification="qualified" if positive and negative else "not_evaluable",
                source_ready=False,
                inventory_ready=False,
                model_ready=False,
            )
            if (
                count != self.descriptor["rows"]
                or digest.hexdigest() != self.descriptor["windows_sha256"]
                or any(self._report.get(k) != v for k, v in expected.items())
            ):
                raise SnapshotError("campaign_stockout_qualification_complete_population_mismatch")
            self._receipt = dict(
                version="ai09-stockout-full-qualification-read-receipt-1.0.0",
                policy=self.policy.model_dump(mode="json"),
                qualification_id=self.manifest["qualification_id"],
                source_dataset_id=self._source["source_dataset_id"],
                public_snapshot_id=self._source["snapshot_id"],
                descriptor_sha256=json_sha256(self.descriptor),
                inventory_sha256=json_sha256(self._seal),
                **expected,
                windows_sha256=digest.hexdigest(),
                ledger_replay_verified=False,
                mature_role_membership_verified=False,
                journal_authorization_proven=False,
                quality_qualified=False,
                stage_ready=False,
            )
            self._exhausted = True
        except BaseException:
            self._failed = True
            raise

    def receipt(self) -> dict[str, Any]:
        if not self._complete or self._failed:
            raise SnapshotError("campaign_stockout_qualification_not_completed")
        return deepcopy(self._receipt)
