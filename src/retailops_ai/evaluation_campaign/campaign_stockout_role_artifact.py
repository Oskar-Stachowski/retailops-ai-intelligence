"""Publish verified private stockout role files; load only a requested role.

The enclosing campaign owns and audits all read/fit permissions. Artifact
verification preserves the recorded preparation receipt; it does not replay
Source independently or authorize training, final access or promotion.
"""

import hashlib
import os
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_stockout_maturity import (
    CampaignStockoutMatureRows,
    _membership,
)
from retailops_ai.evaluation_campaign.campaign_stockout_maturity_contract import STOCKOUT_ROLES
from retailops_ai.evaluation_campaign.campaign_stockout_role_contract import (
    FILES,
    MEMBERSHIP,
    ROLE_FILES,
    CampaignStockoutMembershipRecord,
    CampaignStockoutRoleArtifactPlan,
    CampaignStockoutRoleFile,
    CampaignStockoutRoleRow,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    inventory,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.importer import write_private
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace


class _Trace:
    def __init__(self, *, labels: bool) -> None:
        self.rows = self.size = 0
        self.body, self.origins, self.joined = (hashlib.sha256() for _ in range(3))
        self.keys = hashlib.sha256(b"[")
        self.labels = hashlib.sha256(b"[") if labels else None
        self.previous: tuple[str, str, datetime] | None = None

    def add(self, raw: bytes, key: list[str], joined: str, outcome: int | None = None) -> None:
        normalized = key[0], key[1], datetime.fromisoformat(key[2])
        if self.previous is not None and normalized <= self.previous:
            raise SnapshotError("stockout_role_file_duplicate_or_unsorted_key")
        self.previous = normalized
        if self.rows:
            self.keys.update(b",")
            if self.labels is not None:
                self.labels.update(b",")
        self.keys.update(canonical_bytes(key))
        self.origins.update(canonical_bytes(key) + b"\n")
        self.joined.update(joined.encode("ascii") + b"\n")
        if self.labels is not None:
            if type(outcome) is not int or outcome not in (0, 1):
                raise SnapshotError("stockout_role_requires_known_binary_label")
            self.labels.update(canonical_bytes([*key, outcome]))
        self.body.update(raw)
        self.rows += 1
        self.size += len(raw)

    def receipt(self) -> dict[str, Any]:
        keys = self.keys.copy()
        keys.update(b"]")
        labels = self.labels.copy() if self.labels is not None else None
        if labels is not None:
            labels.update(b"]")
        return CampaignStockoutRoleFile(
            rows=self.rows,
            bytes=self.size,
            sha256=self.body.hexdigest(),
            keys_sha256=keys.hexdigest(),
            origin_stream_sha256=self.origins.hexdigest(),
            joined_row_hashes_sha256=self.joined.hexdigest(),
            labels_sha256=labels.hexdigest() if labels else None,
        ).model_dump(mode="json")


def _joint(raw: dict[str, Any], plan: CampaignStockoutRoleArtifactPlan) -> CampaignStockoutRoleRow:
    row = CampaignStockoutRoleRow.model_validate_json(canonical_bytes(raw))
    if row.membership.model_dump(mode="json") != _membership(
        row.feature, row.label, plan.maturity.policy
    ):
        raise SnapshotError("stockout_role_original_maturity_policy_mismatch")
    return row


def _compact(row: CampaignStockoutRoleRow) -> CampaignStockoutMembershipRecord:
    return CampaignStockoutMembershipRecord(
        product_id=row.label.product_id,
        stock_location_id=row.label.stock_location_id,
        as_of=row.label.as_of,
        membership=row.membership,
        feature_row_sha256=canonical_sha256(row.feature.model_dump(mode="json")),
        label_row_sha256=canonical_sha256(row.label.model_dump(mode="json")),
        joined_row_sha256=canonical_sha256(row.model_dump(mode="json")),
    )


def _key(row: CampaignStockoutMembershipRecord) -> list[str]:
    return [row.product_id, row.stock_location_id, row.model_dump(mode="json")["as_of"]]


def prepare_stockout_roles(
    public: Path,
    snapshot: Path,
    curated: Path,
    qualification: Path,
    target: Path,
    plan: CampaignStockoutRoleArtifactPlan,
    *,
    allow_evaluation_truth: bool = False,
) -> dict[str, Any]:
    if allow_evaluation_truth is not True:
        raise SnapshotError("stockout_role_private_opt_in_required")
    plan = CampaignStockoutRoleArtifactPlan.model_validate(plan.model_dump(mode="python"))
    inputs = (
        checked_directory(public),
        checked_directory(snapshot),
        checked_directory(curated),
        checked_directory(qualification),
    )
    parent = checked_directory(target.absolute().parent)
    target = parent / target.name
    if (
        target.exists()
        or target.is_symlink()
        or any(parent == p or parent.is_relative_to(p) for p in inputs)
    ):
        raise SnapshotError("stockout_role_new_separate_output_required")
    with tempfile.TemporaryDirectory(prefix=".ai09-stockout-roles-", dir=parent) as temporary:
        root = Path(temporary)
        bundle = root / "bundle"
        bundle.mkdir(mode=0o700)
        traces = {name: _Trace(labels=name != MEMBERSHIP) for name in FILES}
        owner = CampaignStockoutMatureRows(
            *inputs, plan.maturity, root, allow_evaluation_truth=True
        )
        total = 0
        whole, whole_bytes = hashlib.sha256(), 0
        with ExitStack() as outputs:
            streams = {
                name: outputs.enter_context(
                    os.fdopen(
                        os.open(bundle / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
                    )
                )
                for name in sorted(FILES)
            }
            with owner:
                for raw in owner.rows():
                    row = _joint(raw, plan)
                    joined_body = canonical_bytes(row.model_dump(mode="json")) + b"\n"
                    whole.update(joined_body)
                    whole_bytes += len(joined_body)
                    compact = _compact(row)
                    body = canonical_bytes(compact.model_dump(mode="json")) + b"\n"
                    if len(body) > plan.max_membership_record_bytes:
                        raise SnapshotError("stockout_role_membership_record_budget")
                    traces[MEMBERSHIP].add(body, _key(compact), compact.joined_row_sha256)
                    streams[MEMBERSHIP].write(body)
                    total += len(body)
                    if row.membership.eligible:
                        role = row.membership.role
                        if role == "purged":
                            raise SnapshotError("stockout_role_purged_row_cannot_be_eligible")
                        name = ROLE_FILES[role]
                        body = joined_body
                        traces[name].add(
                            body,
                            _key(compact),
                            compact.joined_row_sha256,
                            row.label.incident_stockout,
                        )
                        streams[name].write(body)
                        total += len(body)
                    if total > plan.max_artifact_bytes:
                        raise SnapshotError("stockout_role_artifact_budget")
            receipt = owner.receipt()
            if (
                receipt["rows"] != traces[MEMBERSHIP].rows
                or receipt["stream_bytes"] != whole_bytes
                or receipt["stream_sha256"] != whole.hexdigest()
            ):
                raise SnapshotError("stockout_role_original_complete_stream_mismatch")
        descriptor = dict(
            version="ai09-stockout-full-role-artifact-1.0.0",
            plan=plan.model_dump(mode="json"),
            maturity_receipt=receipt,
            files={name: traces[name].receipt() for name in sorted(FILES)},
        )
        manifest: dict[str, Any] = dict(
            dataset_id="ai09-stockout-roles-sha256-" + canonical_sha256(descriptor),
            descriptor=descriptor,
            journal_authorization_proven=False,
            source_generation_ancestry_verified=False,
            quality_qualified=False,
            stage_ready=False,
        )
        manifest_bytes = canonical_bytes(manifest) + b"\n"
        if total + len(manifest_bytes) > plan.max_artifact_bytes:
            raise SnapshotError("stockout_role_artifact_budget")
        write_private(bundle / "manifest.json", manifest_bytes)
        verify_stockout_roles(
            bundle,
            expected_dataset_id=manifest["dataset_id"],
            expected_plan=plan,
            allow_evaluation_truth=True,
        )
        fsync_tree(bundle)
        publish_noreplace(bundle, target)
        return manifest


def _manifest(
    root: Path, expected_dataset_id: str, plan: CampaignStockoutRoleArtifactPlan
) -> tuple[dict[str, Any], bytes]:
    inventory(root, {"manifest.json", *FILES})
    original = read_bytes(root, "manifest.json")
    manifest = decode_json(original)
    descriptor = manifest["descriptor"]
    flags = (
        "journal_authorization_proven",
        "source_generation_ancestry_verified",
        "quality_qualified",
        "stage_ready",
    )
    if (
        set(manifest) != {"dataset_id", "descriptor", *flags}
        or any(manifest[name] is not False for name in flags)
        or canonical_bytes(manifest) + b"\n" != original
        or manifest["dataset_id"] != expected_dataset_id
        or expected_dataset_id != "ai09-stockout-roles-sha256-" + canonical_sha256(descriptor)
        or set(descriptor) != {"version", "plan", "maturity_receipt", "files"}
        or descriptor["version"] != "ai09-stockout-full-role-artifact-1.0.0"
        or descriptor["plan"] != plan.model_dump(mode="json")
        or set(descriptor["files"]) != FILES
    ):
        raise SnapshotError("stockout_role_frozen_manifest_mismatch")
    sizes = 0
    for name, value in descriptor["files"].items():
        file = CampaignStockoutRoleFile.model_validate_json(canonical_bytes(value))
        if file.rows > plan.maturity.max_rows or (file.labels_sha256 is None) != (
            name == MEMBERSHIP
        ):
            raise SnapshotError("stockout_role_file_declaration_mismatch")
        sizes += file.bytes
    if len(original) + sizes > plan.max_artifact_bytes:
        raise SnapshotError("stockout_role_artifact_budget")
    receipt = descriptor["maturity_receipt"]
    if (
        receipt["version"] != "ai09-stockout-full-mature-development-receipt-1.0.0"
        or type(receipt["rows"]) is not int
        or receipt["rows"] < 1
        or type(receipt["fitted_models"]) is not int
        or receipt["fitted_models"] != 0
        or any(receipt[name] is not False for name in flags)
        or receipt["plan_sha256"] != canonical_sha256(plan.maturity.model_dump(mode="json"))
        or receipt["public_dataset_id"] != plan.maturity.public_dataset_id
        or receipt["mature_role_membership_verified"] is not True
        or receipt["complete_physical_origin_join_verified"] is not True
        or receipt["rows"] != descriptor["files"][MEMBERSHIP]["rows"]
        or receipt["original_labels"]["rows"] != receipt["rows"]
        or receipt["original_labels"]["plan_sha256"]
        != canonical_sha256(plan.maturity.labels.model_dump(mode="json"))
        or set(receipt["eligible_by_role"]) != set(STOCKOUT_ROLES)
        or set(receipt["eligible_role_traces"]) != set(STOCKOUT_ROLES)
        or set(receipt["classes_by_role"]) != set(STOCKOUT_ROLES)
        or receipt["original_labels"]["keys_sha256"]
        != descriptor["files"][MEMBERSHIP]["origin_stream_sha256"]
    ):
        raise SnapshotError("stockout_role_original_receipt_binding")
    for role, name in ROLE_FILES.items():
        file, original_role = descriptor["files"][name], receipt["eligible_role_traces"][role]
        classes = receipt["classes_by_role"][role]
        if (
            set(classes) != {"0", "1"}
            or any(type(value) is not int or value < 0 for value in classes.values())
            or type(receipt["eligible_by_role"][role]) is not int
            or sum(classes.values()) != file["rows"]
        ):
            raise SnapshotError("stockout_role_original_class_counts_mismatch")
        if (
            file["rows"] != receipt["eligible_by_role"][role]
            or file["keys_sha256"] != original_role["keys_sha256"]
            or file["labels_sha256"] != original_role["labels_sha256"]
            or file["sha256"] != original_role["joined_rows_sha256"]
        ):
            raise SnapshotError("stockout_role_original_role_trace_mismatch")
    return manifest, original


def _identity(stat: os.stat_result) -> tuple[int, ...]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _file_rows(
    root: Path, name: str, expected: dict[str, Any], plan: CampaignStockoutRoleArtifactPlan
) -> Iterator[CampaignStockoutMembershipRecord | CampaignStockoutRoleRow]:
    membership = name == MEMBERSHIP
    maximum = plan.max_membership_record_bytes if membership else plan.maturity.max_record_bytes
    trace = _Trace(labels=not membership)
    with regular_file(root, name) as stream:
        identity = _identity(os.fstat(stream.fileno()))
        while raw := stream.readline(maximum + 1):
            if len(raw) > maximum:
                raise SnapshotError("stockout_role_record_budget")
            if membership:
                compact = CampaignStockoutMembershipRecord.model_validate_json(raw)
                output: CampaignStockoutMembershipRecord | CampaignStockoutRoleRow = compact
                outcome = None
            else:
                joined_row = _joint(decode_json(raw, limit=maximum), plan)
                compact = _compact(joined_row)
                outcome = joined_row.label.incident_stockout
                if (
                    not joined_row.membership.eligible
                    or joined_row.membership.role + ".jsonl" != name
                ):
                    raise SnapshotError("stockout_role_wrong_or_ineligible_role")
                output = joined_row
            if canonical_bytes(output.model_dump(mode="json")) + b"\n" != raw:
                raise SnapshotError("stockout_role_noncanonical_row")
            trace.add(raw, _key(compact), compact.joined_row_sha256, outcome)
            if trace.rows > plan.maturity.max_rows or trace.size > plan.max_artifact_bytes:
                raise SnapshotError("stockout_role_file_population_budget")
            yield output
        if trace.receipt() != expected:
            raise SnapshotError("stockout_role_complete_file_mismatch")
        if any(
            _identity(info) != identity
            for info in (os.fstat(stream.fileno()), (root / name).stat())
        ):
            raise SnapshotError("stockout_role_file_changed_during_read")


def _end(root: Path, original: bytes, identities: dict[str, tuple[int, ...]]) -> None:
    inventory(root, {"manifest.json", *FILES})
    if read_bytes(root, "manifest.json") != original:
        raise SnapshotError("stockout_role_manifest_changed")
    if any(_identity((root / name).stat()) != value for name, value in identities.items()):
        raise SnapshotError("stockout_role_file_changed_after_read")


def verify_stockout_roles(
    root: Path,
    *,
    expected_dataset_id: str,
    expected_plan: CampaignStockoutRoleArtifactPlan,
    allow_evaluation_truth: bool = False,
) -> dict[str, Any]:
    """Complete private verification; the caller must reserve all five populations."""
    if allow_evaluation_truth is not True:
        raise SnapshotError("stockout_role_private_opt_in_required")
    plan = CampaignStockoutRoleArtifactPlan.model_validate(expected_plan.model_dump(mode="python"))
    manifest, original = _manifest(root, expected_dataset_id, plan)
    descriptor = manifest["descriptor"]
    identities = {name: _identity((root / name).stat()) for name in FILES}
    hashes = {role: hashlib.sha256() for role in STOCKOUT_ROLES}
    counts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for compact in _file_rows(root, MEMBERSHIP, descriptor["files"][MEMBERSHIP], plan):
        if not isinstance(compact, CampaignStockoutMembershipRecord):
            raise SnapshotError("stockout_role_membership_type")
        position = next(
            (
                i
                for i, w in enumerate(plan.maturity.policy.windows)
                if w.origins.start <= compact.as_of.date() <= w.origins.end
            ),
            None,
        )
        window = plan.maturity.policy.windows[position] if position is not None else None
        if compact.membership.role != (
            STOCKOUT_ROLES[position] if position is not None else "purged"
        ) or compact.membership.label_knowledge_cutoff != (
            window.label_knowledge_cutoff if window else None
        ):
            raise SnapshotError("stockout_role_membership_temporal_mapping")
        if compact.membership.eligible:
            role = compact.membership.role
            if role == "purged":
                raise SnapshotError("stockout_role_purged_row_cannot_be_eligible")
            hashes[role].update(compact.joined_row_sha256.encode("ascii") + b"\n")
            counts[role] += 1
        else:
            reasons[compact.membership.reason or ""] += 1
    for role, name in ROLE_FILES.items():
        classes: Counter[int] = Counter()
        for row in _file_rows(root, name, descriptor["files"][name], plan):
            if not isinstance(row, CampaignStockoutRoleRow) or row.label.incident_stockout is None:
                raise SnapshotError("stockout_role_unknown_outcome")
            classes[row.label.incident_stockout] += 1
        file = descriptor["files"][name]
        if (
            counts[role] != file["rows"]
            or hashes[role].hexdigest() != file["joined_row_hashes_sha256"]
            or {str(value): classes[value] for value in (0, 1)}
            != descriptor["maturity_receipt"]["classes_by_role"][role]
        ):
            raise SnapshotError("stockout_role_complete_membership_or_classes_mismatch")
    if dict(sorted(reasons.items())) != descriptor["maturity_receipt"]["ineligible_reasons"]:
        raise SnapshotError("stockout_role_complete_ineligible_reasons_mismatch")
    _end(root, original, identities)
    return manifest


def load_stockout_role(
    root: Path,
    *,
    role: str,
    expected_dataset_id: str,
    expected_plan: CampaignStockoutRoleArtifactPlan,
    allow_evaluation_truth: bool = False,
) -> tuple[list[dict[str, Any]], list[int], dict[str, Any]]:
    """Read one role only; return numeric inputs after complete file acceptance."""
    if allow_evaluation_truth is not True or role not in STOCKOUT_ROLES:
        raise SnapshotError("stockout_role_explicit_private_role_required")
    plan = CampaignStockoutRoleArtifactPlan.model_validate(expected_plan.model_dump(mode="python"))
    manifest, original = _manifest(root, expected_dataset_id, plan)
    name = role + ".jsonl"
    identities = {name: _identity((root / name).stat())}
    rows, outcomes = [], []
    for row in _file_rows(root, name, manifest["descriptor"]["files"][name], plan):
        if not isinstance(row, CampaignStockoutRoleRow) or row.label.incident_stockout is None:
            raise SnapshotError("stockout_role_unknown_outcome")
        feature = row.feature.model_dump(mode="json")
        rows.append(
            {
                k: feature[k]
                for k in ("product_id", "stock_location_id", "as_of", "category_id", "values")
            }
        )
        outcomes.append(row.label.incident_stockout)
    _end(root, original, identities)
    return (
        rows,
        outcomes,
        dict(
            dataset_id=expected_dataset_id,
            role=role,
            rows=len(rows),
            file=manifest["descriptor"]["files"][name],
            complete_role_file_verified=True,
            whole_artifact_verified=False,
            other_role_payloads_read=False,
            journal_authorization_proven=False,
            quality_qualified=False,
            stage_ready=False,
        ),
    )
