"""The planning artifact must reject drift and cannot grant final-test access."""

import hashlib
import json
import stat
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import cli, comparison
from retailops_ai.evaluation_campaign.contract import (
    EvaluationPreparation,
    Repository,
)
from retailops_ai.evaluation_campaign.preparation import (
    default_plan,
    prepare,
    readiness,
    verify_preparation,
    verify_specifications,
)
from retailops_ai.source_snapshot.files import SnapshotError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sources(tmp_path: Path) -> tuple[EvaluationPreparation, dict[Repository, Path]]:
    """Synthetic spec bytes exercise I/O; they are not upstream acceptance evidence."""
    repositories: dict[Repository, Path] = {
        "retailops-cloud-native-platform": tmp_path / "retailops",
        "retailops-ai-intelligence": tmp_path / "ai",
    }
    value = default_plan().model_dump(mode="json")
    for pin in value["specifications"]:
        raw = ("synthetic specification: " + pin["relative_path"] + "\n").encode()
        path = repositories[pin["repository"]] / pin["relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        pin.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    return EvaluationPreparation.model_validate_json(json.dumps(value)), repositories


def test_packaged_plan_matches_tracked_contract_and_schemas() -> None:
    plan = default_plan()
    assert plan.model_dump(mode="json") == json.loads(
        (ROOT / "contracts/evaluation/v1/preparation.default.json").read_text()
    )
    schema = json.loads((ROOT / "contracts/evaluation/v1/preparation.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(plan.model_dump(mode="json"), schema)
    assert plan.data_seeds == (42, 137, 2026)
    assert plan.training_initialization_seeds == (42,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("data_seeds", [42, 137, 2027]),
        ("data_seeds", [42, 42, 2026]),
        ("data_seeds", [137, 42, 2026]),
        ("training_initialization_seeds", []),
        ("training_initialization_seeds", [42, 42]),
        ("training_initialization_seeds", [42, 137, 2026]),
        ("training_initialization_seeds", [True]),
        ("scenarios", ["normal"]),
        ("use_cases", ["forecast", "stockout"]),
        ("final_profile", "ai-temporal-smoke"),
        ("tensorflow_horizons", list(range(1, 14))),
        ("tensorflow_horizons", [True, *range(2, 15)]),
        ("final_test_access_authorized", True),
        ("final_test_access_authorized", 0),
        ("model_promotion_authorized", True),
        ("inherited_ai04_exceptions", True),
        ("requires_upstream_acceptance", False),
        ("preprocessing_fit_scope", "all_rows"),
        ("prediction_population", "intersection_only"),
        ("scope", "final_protocol_approved"),
        ("invented_approval", True),
    ],
)
def test_plan_rejects_weakened_boundaries(field: str, value: object) -> None:
    body = default_plan().model_dump(mode="json")
    body[field] = value
    with pytest.raises(ValidationError):
        EvaluationPreparation.model_validate_json(json.dumps(body))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maximum_trials", 3),
        ("maximum_epochs", 26),
        ("maximum_trial_wall_seconds", 1201),
        ("maximum_process_tree_rss_mib", 1025),
        ("cpu_threads", 2),
        ("cpu_threads", True),
        ("measured_budget_passed", True),
        ("early_stopping", "final_test"),
    ],
)
def test_plan_cannot_raise_initial_tensorflow_budget(field: str, value: object) -> None:
    body = default_plan().model_dump(mode="json")
    body["tensorflow_budget"][field] = value
    with pytest.raises(ValidationError):
        EvaluationPreparation.model_validate_json(json.dumps(body))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "mixed_commit", "unsafe"])
def test_specifications_are_complete_and_revision_bound(mutation: str) -> None:
    body = default_plan().model_dump(mode="json")
    if mutation == "missing":
        body["specifications"].pop()
    elif mutation == "duplicate":
        body["specifications"][0] = body["specifications"][1]
    elif mutation == "mixed_commit":
        body["specifications"][1]["commit_sha"] = "f" * 40
    else:
        body["specifications"][0]["relative_path"] = "../credentials"
    with pytest.raises((ValidationError, SnapshotError)):
        EvaluationPreparation.model_validate_json(json.dumps(body))


def test_prepare_is_immutable_and_never_ready(sources, tmp_path: Path) -> None:
    plan, repositories = sources
    before = {
        p: p.read_bytes() for root in repositories.values() for p in root.rglob("*") if p.is_file()
    }
    destination = prepare(plan, repositories, tmp_path / "output")
    original = destination.read_bytes()
    assert prepare(plan, repositories, tmp_path / "output") == destination
    assert destination.read_bytes() == original
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
    assert not list(destination.parent.glob(".ai09-preparation-*"))
    manifest = verify_preparation(destination, repositories)
    result = readiness(manifest)
    assert result["evaluation_status"] == "not_ready"
    assert result["final_test_access_authorized"] is False
    assert result["model_promotion_authorized"] is False
    assert len(result["blockers"]) == 7
    assert before == {p: p.read_bytes() for p in before}
    schema = json.loads(
        (ROOT / "contracts/evaluation/v1/preparation_manifest.schema.json").read_text()
    )
    jsonschema.validate(manifest.model_dump(mode="json"), schema)


def test_changed_specification_blocks_publication(sources, tmp_path: Path) -> None:
    plan, repositories = sources
    pin = plan.specifications[0]
    (repositories[pin.repository] / pin.relative_path).write_bytes(b"changed rules")
    with pytest.raises(SnapshotError, match="specification_bytes_changed"):
        prepare(plan, repositories, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_specification_symlink_is_rejected(sources, tmp_path: Path) -> None:
    plan, repositories = sources
    pin = plan.specifications[0]
    path = repositories[pin.repository] / pin.relative_path
    other = tmp_path / "external-spec"
    other.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(other)
    with pytest.raises(OSError):
        verify_specifications(plan, repositories)


@pytest.mark.parametrize("mutation", ["id", "runtime", "authorization", "duplicate_json", "format"])
def test_changed_or_resealed_preparation_is_rejected(
    sources, tmp_path: Path, mutation: str
) -> None:
    plan, repositories = sources
    destination = prepare(plan, repositories, tmp_path / "output")
    body = json.loads(destination.read_bytes())
    if mutation == "id":
        body["preparation_id"] = "ai09-preparation-sha256-" + "0" * 64
    elif mutation == "runtime":
        body["descriptor"]["runtime"]["code_sha256"] = "0" * 64
        body["preparation_id"] = "ai09-preparation-sha256-" + canonical_sha256(body["descriptor"])
    elif mutation == "authorization":
        body["descriptor"]["plan"]["final_test_access_authorized"] = True
        body["preparation_id"] = "ai09-preparation-sha256-" + canonical_sha256(body["descriptor"])
    elif mutation == "duplicate_json":
        destination.write_bytes(b'{"preparation_id":"x",' + destination.read_bytes()[1:])
    else:
        destination.write_text(json.dumps(body, indent=2))
    if mutation in {"id", "runtime", "authorization"}:
        destination.write_bytes(canonical_bytes(body) + b"\n")
    corrupted = destination.read_bytes()
    with pytest.raises((SnapshotError, ValidationError)):
        verify_preparation(destination, repositories)
    with pytest.raises((SnapshotError, ValidationError)):
        prepare(plan, repositories, tmp_path / "output")
    assert destination.read_bytes() == corrupted


def test_output_symlink_is_rejected(sources, tmp_path: Path) -> None:
    plan, repositories = sources
    real = tmp_path / "real-output"
    real.mkdir()
    output = tmp_path / "output"
    output.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        prepare(plan, repositories, output)
    assert list(real.iterdir()) == []


def test_changed_staging_cannot_be_published(sources, tmp_path: Path, monkeypatch) -> None:
    from retailops_ai.evaluation_campaign import preparation

    plan, repositories = sources
    output = tmp_path / "output"
    original = preparation.verify_specifications
    calls = 0

    def corrupt_before_publication(plan, repositories):
        nonlocal calls
        original(plan, repositories)
        calls += 1
        if calls == 2:
            next(output.glob(".ai09-preparation-*")).write_bytes(b"corrupted staging")

    monkeypatch.setattr(preparation, "verify_specifications", corrupt_before_publication)
    with pytest.raises(SnapshotError, match="staging_changed"):
        prepare(plan, repositories, output)
    assert list(output.iterdir()) == []


def test_concurrent_publication_reuses_identical_bytes(sources, tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    plan, repositories = sources
    output = tmp_path / "output"
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(prepare, plan, repositories, output) for _ in range(2)]
        paths = [future.result() for future in futures]
    assert paths[0] == paths[1]
    assert len(list(output.iterdir())) == 1
    assert verify_preparation(paths[0], repositories).preparation_id in paths[0].name


def key(
    *, product: str = "p1", location: str = "s1", channel: str = "store", horizon: int = 1
) -> ForecastKey:
    origin = datetime(2026, 6, 1, 23, 59, 59, tzinfo=UTC)
    return ForecastKey.model_validate_json(
        json.dumps(
            {
                "product_id": product,
                "selling_location_id": location,
                "channel": channel,
                "forecast_origin": origin.isoformat(),
                "business_timezone": "UTC",
                "cutoff_policy": "end_of_day_second_v1",
                "horizon_days": horizon,
                "target_date": (date(2026, 6, 1) + timedelta(days=horizon)).isoformat(),
            }
        )
    )


def test_key_alignment_is_order_independent_and_has_complete_grain() -> None:
    keys = [key(horizon=h) for h in range(1, 15)]
    count, digest = comparison.require_same_forecast_keys(iter(keys), reversed(keys))
    assert count == 14
    assert (count, digest) == comparison.require_same_forecast_keys(reversed(keys), keys)
    for changed in [key(product="p2"), key(location="s2"), key(channel="online"), key(horizon=2)]:
        with pytest.raises(SnapshotError, match="population_mismatch"):
            comparison.require_same_forecast_keys([key()], [changed])


@pytest.mark.parametrize("candidate", [[], [key(), key()], [key(horizon=2)]])
def test_key_alignment_rejects_dropped_or_duplicate_rows(candidate) -> None:
    with pytest.raises(SnapshotError):
        comparison.require_same_forecast_keys([key()], candidate)


def test_key_alignment_limit_is_enforced(monkeypatch) -> None:
    monkeypatch.setattr(comparison, "MAX_COMPARISON_KEYS", 1)
    with pytest.raises(SnapshotError, match="key_limit"):
        comparison.require_same_forecast_keys([key(), key(horizon=2)], [key()])


def test_cli_preflight_has_nonzero_not_ready_status(
    sources, tmp_path: Path, monkeypatch, capsys
) -> None:
    plan, repositories = sources
    destination = prepare(plan, repositories, tmp_path / "output")
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluation",
            "preflight",
            "--preparation",
            str(destination),
            "--retailops-repo",
            str(repositories["retailops-cloud-native-platform"]),
            "--ai-repo",
            str(repositories["retailops-ai-intelligence"]),
        ],
    )
    assert cli.main() == 3
    result = json.loads(capsys.readouterr().out)
    assert result["evaluation_status"] == "not_ready"
    assert result["final_test_access_authorized"] is False
