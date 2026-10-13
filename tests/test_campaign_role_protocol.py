"""Real durable journal controls on declared metadata; no Project data or fits."""

import json
from copy import deepcopy
from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from test_ai09_campaign_journal import FULL_COST, complete, selection
from test_campaign_portfolio import document as portfolio_document

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignOperationPlan
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    CampaignPortfolioProtocol,
    PortfolioSourceRecipe,
    parse_campaign_journal,
    parse_campaign_protocol,
)
from retailops_ai.evaluation_campaign.campaign_role_protocol import (
    CALIBRATOR_GRID,
    STOCKOUT_FAMILIES,
    STOCKOUT_VARIANTS,
    RoleAwareCampaignJournal,
    RoleAwareCampaignOperation,
    RoleAwareCampaignProtocol,
)
from retailops_ai.evaluation_campaign.partition_contract import ROLES
from retailops_ai.source_snapshot.files import SnapshotError


def role_document(root):
    value = portfolio_document(root)
    value["role_protocol_version"] = "ai09-three-use-case-role-portfolio-1.0.0"
    dates = (
        ("2025-08-01", "2025-10-31"),
        ("2025-11-16", "2025-11-30"),
        ("2025-12-16", "2025-12-31"),
        ("2026-01-16", "2026-01-31"),
        ("2026-02-16", "2026-07-15"),
    )
    value["development_roles"] = {
        "windows": [
            {
                "role": role,
                "origins": {"start": start, "end": end},
                "label_knowledge_cutoff": (date.fromisoformat(end) + timedelta(days=15)).isoformat()
                + "T23:59:59.999999+00:00",
            }
            for role, (start, end) in zip(ROLES, dates, strict=True)
        ]
    }
    source_operations = [o for o in value["operations"] if o["use_case"] == "source"]
    sources = [
        PortfolioSourceRecipe.model_validate_json(canonical_bytes(s)) for s in value["sources"]
    ]
    operations = []
    readers = {}
    for o in source_operations:
        o["read_roles"] = ["all_parent_data"]
        if o["phase"] == "development":
            operations.append(o)
        if o["action"] == "source_read":
            readers[o["source_recipe_sha256"]] = o["operation_id"]

    def add(name, case, action, role, source, parents=(), **extra):
        digest = source.content_sha256()
        read_roles = [role]
        if action == "model_fit":
            read_roles = {
                "forecast": ["train", "early_stopping"],
                "anomaly": ["train", "tune"],
                "stockout": ["train"],
            }[case]
            extra["initialization_seed"] = 42
        operations.append(
            dict(
                operation_id=name,
                phase=source.phase,
                use_case=case,
                action=action,
                role=role,
                read_roles=read_roles,
                source_recipe_sha256=digest,
                execution_recipe_sha256=canonical_sha256(["controlled-role-recipe", name]),
                prerequisites=[readers[digest], *parents],
                **extra,
            )
        )
        return name

    f, a, s = sources[:3]
    forecasts = [
        add("forecast-fit-" + family, "forecast", "model_fit", "train", f, forecast_family=family)
        for family in ("rf", "hgb", "tensorflow")
    ]
    tune = add("forecast-select", "forecast", "model_score", "tune", f, forecasts)
    forecast = add("forecast-calibrate", "forecast", "calibrator_fit", "calibration", f, (tune,))
    anomaly_fit = add("anomaly-fit", "anomaly", "model_fit", "train", a)
    anomaly = add("anomaly-assess", "anomaly", "model_score", "calibration", a, (anomaly_fit,))
    trials = []
    for i, (family, variant) in enumerate(
        (f, v) for f in STOCKOUT_FAMILIES for v in STOCKOUT_VARIANTS
    ):
        base = add(
            f"stockout-base-{i}",
            "stockout",
            "model_fit",
            "train",
            s,
            stockout_family=family,
            stockout_variant=variant,
        )
        trials.append(base)
        for j, c in enumerate(CALIBRATOR_GRID):
            trials.append(
                add(
                    f"stockout-calibrate-{i}-{j}",
                    "stockout",
                    "calibrator_fit",
                    "early_stopping",
                    s,
                    stockout_family=family,
                    stockout_variant=variant,
                    stockout_calibrator_C=c,
                    resolved_prerequisites=[base],
                )
            )
    selected = add(
        "stockout-select", "stockout", "model_score", "tune", s, resolved_prerequisites=trials
    )
    assessed = add("stockout-assess", "stockout", "model_score", "calibration", s, (selected,))
    for source in sources[:3]:
        for case, parent in (("forecast", forecast), ("anomaly", anomaly), ("stockout", assessed)):
            add(
                f"{source.variant}-evaluate-{case}",
                case,
                "model_score",
                "development_evaluation",
                source,
                (parent,),
            )
    operations.extend(o for o in source_operations if o["phase"] == "final")
    for source in sources[3:]:
        for case in ("forecast", "anomaly", "stockout"):
            add(
                f"final-{source.seed}-{source.variant}-{case}",
                case,
                "model_score",
                "final_evaluation",
                source,
            )
    value["operations"] = operations
    value["maximum_new_attempts"] = len(operations)
    value["maximum_new_fit_attempts"] = sum(
        o["action"] in ("model_fit", "calibrator_fit") for o in operations
    )
    return value


@pytest.fixture
def document(tmp_path):
    return role_document(tmp_path / "journal")


def parse(value):
    return parse_campaign_protocol(canonical_bytes(value))


def operation(value, name):
    return next(o for o in value["operations"] if o["operation_id"] == name)


def test_new_wire_preserves_shared_dates_and_complete_trial_inventory(document):
    protocol = parse(document)
    assert isinstance(protocol, RoleAwareCampaignProtocol)
    assert parse(protocol.model_dump(mode="json")) == protocol
    assert protocol.maximum_new_attempts == 99
    assert protocol.maximum_new_fit_attempts == 35
    assert len({(s.phase, s.seed, s.variant) for s in protocol.sources}) == 12
    roles = protocol.development_roles.windows
    assert tuple(w.role for w in roles) == ROLES
    cal = next(o for o in protocol.operations if o.operation_id == "stockout-calibrate-0-0")
    assert cal.role == "early_stopping" and cal.read_roles == ("early_stopping",)
    old = {
        k: v
        for k, v in cal.model_dump(mode="json").items()
        if k in CampaignOperationPlan.model_fields
    }
    with pytest.raises(ValidationError, match="calibrator_fit_only"):
        CampaignOperationPlan.model_validate_json(canonical_bytes(old))
    with pytest.raises(ValidationError):
        CampaignPortfolioProtocol.model_validate_json(protocol.model_dump_json())


@pytest.mark.parametrize(
    "name,changes",
    [
        ("stockout-base-0", {"read_roles": ["calibration"]}),
        ("stockout-base-0", {"initialization_seed": 137}),
        ("stockout-base-0", {"maximum_attempts": True}),
        ("stockout-base-0", {"maximum_attempts": 2}),
        ("stockout-calibrate-0-0", {"role": "calibration", "read_roles": ["calibration"]}),
        ("stockout-calibrate-0-0", {"stockout_calibrator_C": True}),
        ("stockout-calibrate-0-0", {"stockout_calibrator_C": 0.5}),
        ("stockout-calibrate-0-0", {"resolved_prerequisites": ["stockout-base-1"]}),
        ("stockout-calibrate-0-0", {"resolved_prerequisites": ["stockout-calibrate-0-1"]}),
        ("stockout-select", {"resolved_prerequisites": ["stockout-base-0"]}),
        ("forecast-calibrate", {"role": "early_stopping", "read_roles": ["early_stopping"]}),
        ("anomaly-fit", {"read_roles": ["train", "development_evaluation"]}),
        ("ordinary-evaluate-stockout", {"read_roles": ["tune", "development_evaluation"]}),
    ],
)
def test_wrong_roles_attempts_calibration_or_dependencies_are_rejected(document, name, changes):
    operation(document, name).update(changes)
    with pytest.raises(ValidationError):
        parse(document)


@pytest.mark.parametrize(
    "change",
    [
        "missing_base",
        "missing_calibrator",
        "duplicate_config",
        "missing_assessment",
        "cutoff_crossing",
        "wrong_version",
    ],
)
def test_incomplete_or_changed_population_protocol_is_rejected(document, change):
    if change in ("missing_base", "missing_calibrator", "missing_assessment"):
        target = {
            "missing_base": "stockout-base-0",
            "missing_calibrator": "stockout-calibrate-0-0",
            "missing_assessment": "stockout-assess",
        }[change]
        document["operations"] = [o for o in document["operations"] if o["operation_id"] != target]
        document["maximum_new_attempts"] = len(document["operations"])
        document["maximum_new_fit_attempts"] = sum(
            o["action"] in ("model_fit", "calibrator_fit") for o in document["operations"]
        )
    elif change == "duplicate_config":
        operation(document, "stockout-base-1")["stockout_variant"] = "without_upstream"
    elif change == "cutoff_crossing":
        document["development_roles"]["windows"][0]["label_knowledge_cutoff"] = (
            "2025-11-16T00:00:00+00:00"
        )
    else:
        document["role_protocol_version"] = "ai09-three-use-case-role-portfolio-unknown"
    with pytest.raises(ValidationError):
        parse(document)


def initialize(document):
    from pathlib import Path

    root = Path(document["journal_path"])
    protocol = parse(document)
    actual = journal.initialize(root, protocol)
    assert isinstance(actual, RoleAwareCampaignJournal)
    assert parse_campaign_journal((root / "journal.json").read_bytes()) == actual
    for o in protocol.operations:
        if o.phase == "development" and o.use_case == "source":
            complete(root, o.operation_id)
    return root, protocol


def test_real_journal_waits_for_base_terminal_and_durably_charges_failed_calibrator(document):
    root, _ = initialize(document)
    original = (root / "journal.json").read_bytes()
    with pytest.raises(ValidationError, match="dependency_attempt_must_finish"):
        journal.reserve(root, "stockout-calibrate-0-0")
    assert (root / "journal.json").read_bytes() == original
    base = journal.reserve(root, "stockout-base-0")
    with pytest.raises(ValidationError, match="dependency_attempt_must_finish"):
        journal.reserve(root, "stockout-calibrate-0-0")
    journal.finish(
        root,
        str(base.reservation_id),
        result="failed",
        error_code="controlled_base_failure",
        cost=FULL_COST,
    )
    reserved = journal.reserve(root, "stockout-calibrate-0-0")
    charged = (root / "journal.json").read_bytes()
    with pytest.raises(ValidationError, match="failed_base_cannot"):
        journal.finish(
            root,
            str(reserved.reservation_id),
            result="completed",
            evidence_sha256=canonical_sha256("fake"),
            cost=FULL_COST,
        )
    assert (root / "journal.json").read_bytes() == charged
    journal.finish(
        root,
        str(reserved.reservation_id),
        result="failed",
        error_code="controlled_base_unavailable",
        cost=FULL_COST,
    )
    report = journal.summary(root)
    assert report["charged_new_fit_attempts"] == 2 and report["failed_new_attempts"] == 2
    with pytest.raises(ValidationError, match="budget_exhausted"):
        journal.reserve(root, "stockout-calibrate-0-0")
    with pytest.raises(ValidationError, match="dependency_attempt_must_finish"):
        journal.reserve(root, "stockout-select")


def test_full_development_inventory_freeze_and_final_follow_original_guards(document):
    root, protocol = initialize(document)
    with pytest.raises(ValidationError, match="phase_mismatch"):
        journal.reserve(root, "final-42-ordinary-forecast")
    with pytest.raises(ValidationError):
        journal.freeze_selection(root, selection(root))
    for o in protocol.operations:
        if o.phase == "development" and o.use_case != "source":
            complete(root, o.operation_id)
    journal.freeze_selection(root, selection(root))
    assert journal.summary(root)["selection_frozen"]
    for o in protocol.operations:
        if o.phase == "final":
            complete(root, o.operation_id)
    journal.close(root, canonical_sha256("controlled-declaration-report-not-real-quality"))
    report = journal.summary(root)
    assert report["execution_closed"] and report["charged_new_fit_attempts"] == 35
    assert report["completed_new_attempts"] == 99 and not report["stage_ready"]
    assert not journal.inspect(root).final_holdout_freshness_qualified


def test_existing_started_declaration_cannot_be_extended_in_place(document):
    root, _ = initialize(document)
    before = (root / "journal.json").read_bytes()
    changed = deepcopy(document)
    changed["segment_policy_sha256"] = canonical_sha256("different-policy")
    with pytest.raises(SnapshotError, match="already_frozen"):
        journal.initialize(root, parse(changed))
    assert (root / "journal.json").read_bytes() == before
    disk = json.loads(before)
    assert "role_protocol_version" in disk["protocol"]
    assert disk["protocol"]["operations"][0]["read_roles"] == ["all_parent_data"]


def test_copied_operation_bool_attempt_is_revalidated(document):
    op = RoleAwareCampaignOperation.model_validate_json(
        canonical_bytes(operation(document, "stockout-base-0"))
    )
    copied = op.model_copy(update={"maximum_attempts": True})
    with pytest.raises(ValidationError):
        RoleAwareCampaignOperation.model_validate(copied.model_dump(mode="python"))


def test_copied_nested_bool_cannot_be_normalized_before_initialization_io(document, monkeypatch):
    from pathlib import Path

    protocol = parse(document)
    assert isinstance(protocol, RoleAwareCampaignProtocol)
    changed = list(protocol.operations)
    position = next(i for i, op in enumerate(changed) if op.operation_id == "stockout-base-0")
    changed[position] = changed[position].model_copy(update={"maximum_attempts": True})
    copied = protocol.model_copy(update={"operations": tuple(changed)})
    monkeypatch.setattr(
        journal, "_runtime", lambda *a: pytest.fail("invalid declaration reached runtime I/O")
    )
    with pytest.raises(ValidationError):
        copied.content_sha256()
    with pytest.raises(ValidationError):
        journal.initialize(Path(document["journal_path"]), copied)
    assert not Path(document["journal_path"]).exists()


def test_actual_source_label_delay_cannot_use_shorter_default_maturity(document):
    replacements = {}
    for source in document["sources"][:3]:
        original = PortfolioSourceRecipe.model_validate_json(
            canonical_bytes(source)
        ).content_sha256()
        source["label_delay_days"] = 2
        replacements[original] = PortfolioSourceRecipe.model_validate_json(
            canonical_bytes(source)
        ).content_sha256()
    for op in document["operations"]:
        op["source_recipe_sha256"] = replacements.get(
            op["source_recipe_sha256"], op["source_recipe_sha256"]
        )
    document["training_source_recipe_sha256"] = {
        case: replacements[digest]
        for case, digest in document["training_source_recipe_sha256"].items()
    }
    with pytest.raises(ValidationError, match="label_cutoff_before_maturity"):
        parse(document)
