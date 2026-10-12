"""Private role publication/read boundaries on exposed full Source controls."""

import json
import shutil
from datetime import datetime
from types import SimpleNamespace

import pytest
from test_campaign_anomaly_parent import public_parent as public_parent  # noqa: F401
from test_campaign_stockout_labels import qualified as qualified  # noqa: F401
from test_campaign_stockout_labels import reference
from test_campaign_stockout_maturity import feature, label, policy, reader
from test_campaign_stockout_public import prepared as prepared  # noqa: F401

from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_stockout_role_artifact as component
from retailops_ai.evaluation_campaign.campaign_stockout_maturity import _membership
from retailops_ai.evaluation_campaign.campaign_stockout_maturity_contract import STOCKOUT_ROLES
from retailops_ai.evaluation_campaign.campaign_stockout_role_contract import (
    FILES,
    MEMBERSHIP,
    ROLE_FILES,
    CampaignStockoutRoleArtifactPlan,
)
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout_training.inputs import keys_sha256, labels_sha256


@pytest.fixture(scope="module")
def roles(public_parent, prepared, qualified, tmp_path_factory):
    root = tmp_path_factory.mktemp("ai09-stockout-role-artifact")
    maturity = reader(public_parent, prepared, qualified, root).plan
    plan = CampaignStockoutRoleArtifactPlan(maturity=maturity)
    target = root / "bundle"
    result = component.prepare_stockout_roles(
        prepared[0],
        *public_parent[:2],
        qualified[0],
        target,
        plan,
        allow_evaluation_truth=True,
    )
    return target, plan, result


def verify(case):
    target, plan, manifest = case
    return component.verify_stockout_roles(
        target,
        expected_dataset_id=manifest["dataset_id"],
        expected_plan=plan,
        allow_evaluation_truth=True,
    )


def load(case, role):
    target, plan, manifest = case
    return component.load_stockout_role(
        target,
        role=role,
        expected_dataset_id=manifest["dataset_id"],
        expected_plan=plan,
        allow_evaluation_truth=True,
    )


def test_all_original_memberships_and_role_inputs_match_native_labels(
    public_parent, prepared, qualified, roles
):
    target, plan, manifest = roles
    labels = reference(public_parent, qualified)
    features = [json.loads(line) for line in (prepared[0] / "rows.jsonl").read_bytes().splitlines()]
    memberships = [json.loads(line) for line in (target / MEMBERSHIP).read_bytes().splitlines()]

    def key(row):
        return row["product_id"], row["stock_location_id"], row["as_of"]

    assert list(map(key, memberships)) == list(map(key, features)) == list(map(key, labels))
    assert verify(roles) == manifest
    assert set(p.name for p in target.iterdir()) == {"manifest.json", *FILES}
    assert target.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in target.iterdir())
    for role, window in zip(STOCKOUT_ROLES, plan.maturity.policy.windows, strict=True):
        expected, outcomes = [], []
        for f, point in zip(features, labels, strict=True):
            if (
                window.origins.start
                <= datetime.fromisoformat(point["as_of"]).date()
                <= window.origins.end
                and f["status"] == "eligible"
                and point["status"] == "evaluable"
                and datetime.fromisoformat(point["label_available_at"])
                <= window.label_knowledge_cutoff
            ):
                expected.append(
                    {
                        k: f[k]
                        for k in (
                            "product_id",
                            "stock_location_id",
                            "as_of",
                            "category_id",
                            "values",
                        )
                    }
                )
                outcomes.append(point["incident_stockout"])
        rows, ys, receipt = load(roles, role)
        assert rows == expected and ys == outcomes
        assert receipt["file"]["keys_sha256"] == keys_sha256(rows)
        assert receipt["file"]["labels_sha256"] == labels_sha256(rows, ys)
        assert receipt["rows"] == len(rows)
        assert receipt["complete_role_file_verified"]
        assert not receipt["whole_artifact_verified"] and not receipt["other_role_payloads_read"]
        assert not receipt["journal_authorization_proven"] and not receipt["stage_ready"]
    assert not manifest["stage_ready"]


def test_loader_opens_only_requested_role_payload(roles, monkeypatch):
    actual = component.regular_file
    opened = []

    def recording(root, name):
        opened.append(name)
        return actual(root, name)

    monkeypatch.setattr(component, "regular_file", recording)
    load(roles, "base_train")
    assert opened == [ROLE_FILES["base_train"]]


@pytest.mark.parametrize("name", sorted(FILES))
def test_every_file_is_verified_including_empty_roles(roles, tmp_path, name):
    root = tmp_path / "bundle"
    shutil.copytree(roles[0], root)
    target = root / name
    target.write_bytes(target.read_bytes() + b"{}\n")
    with pytest.raises((SnapshotError, ValueError)):
        verify((root, *roles[1:]))


def test_membership_changed_while_other_roles_are_verified_is_refused(roles, tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    shutil.copytree(roles[0], root)
    actual = component._file_rows

    def changed(path, name, expected, plan):
        if name == ROLE_FILES["base_train"]:
            target = root / MEMBERSHIP
            target.write_bytes(target.read_bytes() + b" ")
        yield from actual(path, name, expected, plan)

    monkeypatch.setattr(component, "_file_rows", changed)
    with pytest.raises(SnapshotError, match="changed_after_read"):
        verify((root, *roles[1:]))


def test_loader_checks_selected_file_after_generator_closes(roles, tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    shutil.copytree(roles[0], root)
    actual = component._file_rows

    def changed(path, name, expected, plan):
        yield from actual(path, name, expected, plan)
        target = root / name
        target.write_bytes(target.read_bytes() + b" ")

    monkeypatch.setattr(component, "_file_rows", changed)
    with pytest.raises(SnapshotError, match="changed_after_read"):
        load((root, *roles[1:]), "base_train")


@pytest.mark.parametrize("mutation", ["identity", "typed_count", "role_hash", "plan"])
def test_resealed_metadata_does_not_bypass_original_role_receipts(roles, tmp_path, mutation):
    root = tmp_path / "bundle"
    shutil.copytree(roles[0], root)
    path = root / "manifest.json"
    document = json.loads(path.read_bytes())
    if mutation == "typed_count":
        document["descriptor"]["files"][MEMBERSHIP]["rows"] = True
    elif mutation == "role_hash":
        document["descriptor"]["files"][ROLE_FILES["base_train"]]["keys_sha256"] = "a" * 64
    elif mutation == "plan":
        document["descriptor"]["plan"]["max_artifact_bytes"] -= 1
    else:
        document["descriptor"]["maturity_receipt"]["public_dataset_id"] = (
            "ai09-stockout-public-sha256-" + "a" * 64
        )
    document["dataset_id"] = "ai09-stockout-roles-sha256-" + canonical_sha256(
        document["descriptor"]
    )
    path.write_bytes(canonical_bytes(document) + b"\n")
    with pytest.raises((SnapshotError, ValueError)):
        verify((root, roles[1], document))


@pytest.mark.parametrize("operation", ["prepare", "verify", "load"])
def test_explicit_private_opt_in_precedes_all_io(roles, tmp_path, monkeypatch, operation):
    monkeypatch.setattr(component, "inventory", lambda *a, **k: pytest.fail("I/O"))
    monkeypatch.setattr(component, "checked_directory", lambda *a, **k: pytest.fail("I/O"))
    target, plan, manifest = roles
    with pytest.raises(SnapshotError, match="private"):
        if operation == "prepare":
            component.prepare_stockout_roles(target, target, target, target, tmp_path / "out", plan)
        elif operation == "verify":
            component.verify_stockout_roles(
                target, expected_dataset_id=manifest["dataset_id"], expected_plan=plan
            )
        else:
            component.load_stockout_role(
                target,
                role="base_train",
                expected_dataset_id=manifest["dataset_id"],
                expected_plan=plan,
            )


def test_child_exit_failure_prevents_publication_and_cleans_private_files(
    public_parent, prepared, qualified, roles, tmp_path, monkeypatch
):
    origin = end_of_day(roles[1].maturity.policy.windows[0].origins.start)
    f, point = feature(origin), label(origin)
    row = dict(
        feature=f.model_dump(mode="json"),
        label=point.model_dump(mode="json"),
        membership=_membership(f, point, roles[1].maturity.policy),
    )

    class FailingOwner:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def rows(self):
            yield row

        def __exit__(self, *args):
            raise SnapshotError("controlled_child_final_check_failed")

    monkeypatch.setattr(component, "CampaignStockoutMatureRows", FailingOwner)
    with pytest.raises(SnapshotError, match="child_final_check"):
        component.prepare_stockout_roles(
            prepared[0],
            *public_parent[:2],
            qualified[0],
            tmp_path / "out",
            roles[1],
            allow_evaluation_truth=True,
        )
    assert not list(tmp_path.iterdir())


def test_copied_bool_budget_is_rejected_before_io(roles, tmp_path, monkeypatch):
    monkeypatch.setattr(component, "checked_directory", lambda *a, **k: pytest.fail("I/O"))
    plan = roles[1].model_copy(update={"max_artifact_bytes": True})
    with pytest.raises(ValueError):
        component.prepare_stockout_roles(
            tmp_path,
            tmp_path,
            tmp_path,
            tmp_path,
            tmp_path / "out",
            plan,
            allow_evaluation_truth=True,
        )


def test_more_than_10000_actual_role_records_are_read_without_sampling(tmp_path):
    """Synthetic file/reader control only; not original Source or capacity evidence."""
    frozen = policy()
    origin = end_of_day(frozen.windows[0].origins.start)
    plan = SimpleNamespace(
        maturity=SimpleNamespace(policy=frozen, max_rows=10001, max_record_bytes=65536),
        max_artifact_bytes=64 * 1024**2,
    )
    name = ROLE_FILES["base_train"]
    trace = component._Trace(labels=True)
    with (tmp_path / name).open("wb") as stream:
        for i in range(10001):
            f, point = feature(origin, product=f"p-{i:06d}"), label(origin, product=f"p-{i:06d}")
            row = component._joint(
                dict(
                    feature=f.model_dump(mode="json"),
                    label=point.model_dump(mode="json"),
                    membership=_membership(f, point, frozen),
                ),
                plan,
            )
            compact = component._compact(row)
            raw = canonical_bytes(row.model_dump(mode="json")) + b"\n"
            stream.write(raw)
            trace.add(raw, component._key(compact), compact.joined_row_sha256, 0)
    assert sum(1 for _ in component._file_rows(tmp_path, name, trace.receipt(), plan)) == 10001
    plan.maturity.max_rows = 10000
    with pytest.raises(SnapshotError, match="population_budget"):
        sum(1 for _ in component._file_rows(tmp_path, name, trace.receipt(), plan))
