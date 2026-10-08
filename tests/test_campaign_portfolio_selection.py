"""Real durable journal/artifact guards with declared scientific receipt doubles.

These controls do not qualify model quality, full Source data or Project fits.
Forecast parsing/scientific verification is substituted explicitly; production
uses the existing typed parser/verifier. Other-use artifact checks are real.
"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_ai09_campaign_journal import complete, protocol_document, selection
from test_campaign_portfolio import document, refresh_budget

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_evaluation, campaign_journal
from retailops_ai.evaluation_campaign import campaign_selection_evidence as verifier
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost, SelectionFreeze
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import parse_campaign_protocol
from retailops_ai.source_snapshot.files import SnapshotError


def controlled_freeze(tmp_path, monkeypatch, *, change=None, portfolio=True):
    root = tmp_path / "journal"
    value = document(root) if portfolio else protocol_document(root)
    if not portfolio:
        source = value["sources"][0]
        source_sha = canonical_sha256(source)
        start = next(i for i, o in enumerate(value["operations"]) if o["phase"] == "final")
        value["operations"][start:start] = [
            {
                "operation_id": "development-evaluate-" + case,
                "phase": "development",
                "action": "model_score",
                "use_case": case,
                "role": "development_evaluation",
                "source_recipe_sha256": source_sha,
                "execution_recipe_sha256": canonical_sha256(case),
                "prerequisites": ["development-42-read", "development-fit"],
            }
            for case in ("forecast", "anomaly", "stockout")
        ]
    recipes = {}
    for operation in value["operations"]:
        if operation["phase"] == "development" and operation["action"] == "model_score":
            name = operation["operation_id"]
            recipes[name] = {"control": "selection-boundary", "operation_id": name}
            operation["execution_recipe_sha256"] = canonical_sha256(recipes[name])
            if change == "ambiguous" and name == "development-42-physical-evaluate-stockout":
                operation["maximum_attempts"] = 2
    refresh_budget(value)
    protocol = parse_campaign_protocol(canonical_bytes(value))
    campaign_journal.initialize(root, protocol)
    (root / "receipts").mkdir(mode=0o700)
    components = {
        b.use_case: b.model_dump(mode="json", exclude={"use_case", "selection_evidence_sha256"})
        for b in selection(root).bundles
    }
    records = {}
    selected = {}
    bundles = {}
    for operation in protocol.operations:
        if operation.phase != "development":
            continue
        if operation.action != "model_score":
            complete(root, operation.operation_id)
            continue
        name = operation.operation_id
        bundle = tmp_path / name
        bundle.mkdir(mode=0o700)
        artifact = bundle / "report.json"
        artifact.write_bytes(canonical_bytes({"declared_control": name}) + b"\n")
        artifact.chmod(0o600)
        files, size = _bundle_inventory(bundle, 4096)
        attempts = 2 if change == "ambiguous" and operation.maximum_attempts == 2 else 1
        for _ in range(attempts):
            with campaign_journal.audited_operation(root, name) as audit:
                receipt = {
                    "version": "ai09-campaign-" + operation.use_case + "-evaluation-receipt-1.0.0",
                    "operation_id": name,
                    "reservation_id": audit.reservation.reservation_id,
                    "use_case": operation.use_case,
                    "protocol_sha256": protocol.content_sha256(),
                    "runtime_code_sha256": protocol.runtime.code_sha256,
                    "plan": {
                        "phase": "development",
                        "role": "development_evaluation",
                        "source_recipe_sha256": operation.source_recipe_sha256,
                        "max_output_bytes": 4096,
                    },
                    "recipe": recipes[name],
                    "critical_segment_inventory_complete": True,
                    "block_uncertainty_complete": True,
                    "quality_qualified": True,
                    "final_test_accessed": False,
                    "selection_components": deepcopy(components[operation.use_case]),
                    "artifact_files": files,
                    "artifact_sha256": canonical_sha256(files),
                    "artifact_bytes": size,
                    "configuration": {"declared_control": "same-frozen-configuration"},
                }
                if name == "development-42-physical-evaluate-stockout":
                    if change in (
                        "quality_qualified",
                        "critical_segment_inventory_complete",
                        "block_uncertainty_complete",
                    ):
                        receipt[change] = False
                    elif change == "final_test_accessed":
                        receipt[change] = True
                    elif change == "components":
                        receipt["selection_components"]["calibration_sha256"] = "0" * 64
                    elif change == "recipe":
                        receipt["recipe"] = {"changed": True}
                    elif change == "source":
                        receipt["plan"]["source_recipe_sha256"] = "0" * 64
                    elif change == "version":
                        receipt["version"] = "unknown-receipt"
                if (
                    change == "configuration"
                    and name == "development-42-physical-evaluate-forecast"
                ):
                    receipt["configuration"] = {"declared_control": "different-configuration"}
                stored = root / "receipts" / (audit.reservation.reservation_id + ".json")
                stored.write_bytes(canonical_bytes(receipt) + b"\n")
                stored.chmod(0o600)
                audit.evidence_sha256 = canonical_sha256(receipt)
                audit.cost = CampaignCost(
                    wall_seconds=0.1, peak_process_tree_rss_bytes=1024, artifact_bytes=size
                )
                records[name] = (receipt, stored)
        bundles[name] = bundle
        selected.setdefault(operation.use_case, (receipt, bundle))
    fields = selection(root).model_dump(mode="json")
    for chosen in fields["bundles"]:
        receipt, bundle = selected[chosen["use_case"]]
        chosen["selection_evidence_sha256"] = canonical_sha256(receipt)
        bundles[chosen["use_case"]] = bundle
    frozen = SelectionFreeze.model_validate_json(canonical_bytes(fields))
    campaign_journal.freeze_selection(root, frozen)
    calls = []

    def parse(raw):
        import json

        receipt = json.loads(raw)
        return SimpleNamespace(configuration=receipt["configuration"], value=receipt)

    def scientific_control(bundle, *, journal, receipt):
        assert journal == root
        calls.append(receipt.value["operation_id"])
        files, size = _bundle_inventory(bundle, 4096)
        if files != receipt.value["artifact_files"] or size != receipt.value["artifact_bytes"]:
            raise SnapshotError("controlled_forecast_artifact_mismatch")

    monkeypatch.setattr(verifier, "parse_forecast_evaluation_receipt", parse)
    monkeypatch.setattr(
        campaign_evaluation, "verify_campaign_forecast_evaluation", scientific_control
    )
    if not portfolio:
        bundles = {case: bundles[case] for case in selected}
    return root, bundles, records, frozen, calls


def test_all_nine_receipts_and_actual_artifacts_preserve_original_freeze_digest(
    tmp_path, monkeypatch
):
    root, bundles, _, frozen, calls = controlled_freeze(tmp_path, monkeypatch)
    before = (root / "journal.json").read_bytes()
    digest, receipt = verifier.verify_completed_campaign_selection(root, bundles)
    assert digest == canonical_sha256(frozen.model_dump(mode="json"))
    assert len(bundles) == 12
    assert calls == [
        "development-42-ordinary-evaluate-forecast",
        "development-42-ordinary-evaluate-forecast",
        "development-42-demand-evaluate-forecast",
        "development-42-physical-evaluate-forecast",
    ]
    assert receipt.configuration == {"declared_control": "same-frozen-configuration"}
    assert (root / "journal.json").read_bytes() == before
    assert not campaign_journal.inspect(root).stage_ready


@pytest.mark.parametrize(
    "change",
    [
        "quality_qualified",
        "critical_segment_inventory_complete",
        "block_uncertainty_complete",
        "final_test_accessed",
        "components",
        "recipe",
        "source",
        "version",
        "configuration",
        "ambiguous",
    ],
)
def test_unselected_variant_cannot_escape_qualification_or_frozen_components(
    tmp_path, monkeypatch, change
):
    root, bundles, _, _, _ = controlled_freeze(tmp_path, monkeypatch, change=change)
    before = (root / "journal.json").read_bytes()
    with pytest.raises(SnapshotError):
        verifier.verify_completed_campaign_selection(root, bundles)
    assert (root / "journal.json").read_bytes() == before


@pytest.mark.parametrize(
    "attack", ["missing_bundle", "artifact", "receipt", "permissions", "symlink"]
)
def test_unselected_artifact_or_private_receipt_attack_rejected(tmp_path, monkeypatch, attack):
    root, bundles, records, _, _ = controlled_freeze(tmp_path, monkeypatch)
    name = "development-42-physical-evaluate-stockout"
    _, stored = records[name]
    if attack == "missing_bundle":
        del bundles[name]
    elif attack == "artifact":
        (bundles[name] / "report.json").write_bytes(b"changed-artifact\n")
    elif attack == "receipt":
        stored.write_bytes(
            stored.read_bytes().replace(b'"quality_qualified":true', b'"quality_qualified":false')
        )
    elif attack == "permissions":
        stored.chmod(0o644)
    else:
        copied = tmp_path / "symlink-target.json"
        copied.write_bytes(stored.read_bytes())
        copied.chmod(0o600)
        stored.unlink()
        stored.symlink_to(copied)
    before = (root / "journal.json").read_bytes()
    with pytest.raises(SnapshotError):
        verifier.verify_completed_campaign_selection(root, bundles)
    assert (root / "journal.json").read_bytes() == before


def test_legacy_three_bundle_boundary_and_original_digest_unchanged(tmp_path, monkeypatch):
    root, bundles, _, frozen, calls = controlled_freeze(tmp_path, monkeypatch, portfolio=False)
    digest, _ = verifier.verify_completed_campaign_selection(root, bundles)
    assert set(bundles) == {"forecast", "anomaly", "stockout"}
    assert digest == canonical_sha256(frozen.model_dump(mode="json"))
    assert calls == ["development-evaluate-forecast"]
