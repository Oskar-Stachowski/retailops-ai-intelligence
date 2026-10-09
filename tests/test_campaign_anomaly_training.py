"""Real full-role fit parity, no sampling, and closed failure paths."""

import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import pytest
from test_anomaly_detectors import scope
from test_campaign_anomaly_membership import complete_case, plan  # noqa: F401

from retailops_ai.anomaly_detectors.census_contract import CensusFitPolicy
from retailops_ai.anomaly_detectors.codec import baseline_score, forest_scores
from retailops_ai.anomaly_detectors.contract import FitPolicy
from retailops_ai.anomaly_detectors.engine import capacity_threshold
from retailops_ai.anomaly_detectors.fit import fit_pipeline
from retailops_ai.anomaly_portfolio.model import EventCapacity
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import campaign_anomaly_training as training
from retailops_ai.evaluation_campaign.campaign_anomaly_membership import (
    iter_anomaly_membership_census,
)
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json


def capacities():
    return tuple(
        EventCapacity(
            event_type=e, alert_fraction=0.2 if e == "sale_completed" else 0.1, high_fraction=0.05
        )
        for e in ("sale_completed", "return_completed")
    )


def fit(rows, cfg, tmp_path, **kwargs):
    return training.fit_anomaly_membership_census(
        rows,
        cfg,
        CensusFitPolicy(n_estimators=8, max_samples=16),
        capacities(),
        scratch=tmp_path,
        **kwargs,
    )


def test_complete_roles_match_original_fit_threshold_and_membership_hashes(complete_case, tmp_path):  # noqa: F811
    cfg, points = complete_case
    rows = list(iter_anomaly_membership_census(points, cfg))
    result = fit(iter(rows), cfg, tmp_path)
    assert result.requested_rows == cfg.rows
    assert sum(c for _, _, c in result.membership_counts) == cfg.rows
    assert result.membership_plan_sha256 == canonical_sha256(cfg.model_dump(mode="json"))
    for role in ("train", "validation"):
        assert getattr(
            result, role.replace("train", "training") + "_membership_sha256"
        ) == canonical_sha256(
            [r.membership.model_dump(mode="json") for r in rows if r.membership.role == role]
        )
    for group in result.groups:
        selected = [
            r
            for r in rows
            if (r.membership.event_type, r.membership.currency)
            == (group.event_type, group.currency)
        ]
        train = [
            r.training_or_validation_row
            for r in selected
            if r.membership.role == "train" and r.membership.eligible
        ]
        validation = [
            r.training_or_validation_row
            for r in selected
            if r.membership.role == "validation" and r.membership.eligible
        ]
        native, _ = fit_pipeline(train, validation, FitPolicy(n_estimators=8, max_samples=16))
        assert group.pipeline.model_dump() == native.model_dump()
        c = next(c for c in capacities() if c.event_type == group.event_type)
        policy = FitPolicy(
            validation_alert_fraction=c.alert_fraction, validation_high_fraction=c.high_fraction
        )
        assert (
            group.baseline_threshold.model_dump()
            == capacity_threshold([baseline_score(r) for r in validation], policy).model_dump()
        )
        assert (
            group.forest_threshold.model_dump()
            == capacity_threshold(list(forest_scores(native, validation)), policy).model_dump()
        )
    assert len(result.fit_resources) == 2
    assert all(r.cpu_seconds > 0 for r in result.fit_resources)
    assert not list(tmp_path.iterdir())


def test_more_than_10000_eligible_training_rows_are_all_used(complete_case, tmp_path):  # noqa: F811
    cfg, points = complete_case
    template = [
        r
        for r in iter_anomaly_membership_census(points, cfg)
        if r.membership.event_type == "sale_completed"
    ]
    scopes = tuple(scope(product=str(UUID(int=i + 1))) for i in range(501))
    cfg = plan(scopes)
    consumed = 0
    digest_rows = []

    def rows():
        nonlocal consumed
        for s in scopes:
            for value in template:
                member = value.membership.model_copy(update={"product_id": s.product_id})
                vector = value.training_or_validation_row
                if member.role == "train" and vector is not None:
                    vector = vector.model_copy(
                        update={"planned_price": 1.0 if s == scopes[0] else 101.0}
                    )
                    digest_rows.append(vector.model_dump(mode="json"))
                consumed += 1
                yield replace(value, membership=member, training_or_validation_row=vector)

    result = fit(rows(), cfg, tmp_path)
    pipeline = result.groups[0].pipeline
    assert consumed == result.requested_rows == cfg.rows
    assert pipeline.training_rows == 10020
    assert pipeline.training_rows_sha256 == canonical_sha256(digest_rows)
    assert next(f for f in pipeline.fills if f.name == "planned_price").value == 101.0
    assert result.groups[0].baseline_threshold.validation_rows == 8016
    assert result.groups[0].forest_threshold.validation_rows == 8016
    assert not list(tmp_path.iterdir())


def test_unknown_population_and_empty_groups_remain_explicit(tmp_path):
    cfg = plan()
    result = fit(iter_anomaly_membership_census([], cfg), cfg, tmp_path)
    assert result.requested_rows == cfg.rows
    assert all(not eligible for _, eligible, _ in result.membership_counts)
    assert not result.fit_resources and result.role_file_bytes == 0
    group = result.groups[0]
    assert group.training_rows == 0 and group.pipeline is None
    assert group.baseline_threshold is None and group.forest_threshold is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "order",
        "missing_vector",
        "test_vector",
        "clock",
        "cutoff",
        "event",
        "point_digest",
        "budget",
    ],
)
def test_incomplete_or_invalid_roles_reject_before_any_fit(
    change,
    complete_case,  # noqa: F811
    tmp_path,
    monkeypatch,  # noqa: F811
):  # noqa: F811
    cfg, points = complete_case
    rows = list(iter_anomaly_membership_census(points, cfg))

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid complete population must not start a fit")

    monkeypatch.setattr(training, "fit_census_pipeline", forbidden)
    ready = next(i for i, r in enumerate(rows) if r.training_or_validation_row is not None)
    kwargs = {}
    if change == "missing":
        rows.pop()
    elif change == "extra":
        rows.append(rows[-1])
    elif change == "order":
        rows[0], rows[1] = rows[1], rows[0]
    elif change == "missing_vector":
        rows[ready] = replace(rows[ready], training_or_validation_row=None)
    elif change == "test_vector":
        i = next(i for i, r in enumerate(rows) if r.membership.role == "test")
        rows[i] = replace(
            rows[i], training_or_validation_row=rows[ready].training_or_validation_row
        )
    elif change == "clock":
        rows[0] = replace(
            rows[0],
            membership=rows[0].membership.model_copy(
                update={"scoring_origin": cfg.training_cutoff}
            ),
        )
    elif change == "cutoff":
        i = next(
            i
            for i, r in enumerate(rows)
            if "outcome_after_training_cutoff" in r.membership.reason_codes
        )
        rows[i] = replace(
            rows[i],
            membership=rows[i].membership.model_copy(update={"eligible": True, "reason_codes": ()}),
            training_or_validation_row=rows[ready].training_or_validation_row,
        )
    elif change == "event":
        vector = rows[ready].training_or_validation_row
        other = "sale_completed" if vector.event_type == "return_completed" else "return_completed"
        rows[ready] = replace(
            rows[ready], training_or_validation_row=vector.model_copy(update={"event_type": other})
        )
    elif change == "point_digest":
        rows[ready] = replace(rows[ready], public_point_sha256="bad")
    elif change == "budget":
        kwargs["max_role_bytes"] = 4096
    with pytest.raises((ValueError, SnapshotError)):
        fit(rows, cfg, tmp_path, **kwargs)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("change", ["truncate", "append", "replace", "symlink"])
def test_corrupted_private_role_never_returns_result(change, complete_case, tmp_path, monkeypatch):  # noqa: F811
    cfg, points = complete_case
    original = training._RoleFile.rows
    altered = False

    def corrupt(self):
        nonlocal altered
        if not altered and self.count:
            altered = True
            raw = self.path.read_bytes()
            if change == "truncate":
                self.path.write_bytes(raw[: raw.index(b"\n") + 1])
            elif change == "append":
                self.path.write_bytes(raw + raw[: raw.index(b"\n") + 1])
            elif change == "replace":
                first, rest = raw.split(b"\n", 1)
                value = json.loads(first)
                value["planned_price"] = 1.0 if value["planned_price"] != 1.0 else 2.0
                self.path.write_bytes(canonical_json(value) + b"\n" + rest)
            else:
                other = self.path.with_suffix(".copy")
                other.write_bytes(raw)
                self.path.unlink()
                self.path.symlink_to(other)
        yield from original(self)

    monkeypatch.setattr(training._RoleFile, "rows", corrupt)
    with pytest.raises((ValueError, SnapshotError)):
        fit(iter_anomaly_membership_census(points, cfg), cfg, tmp_path)
    assert altered and not list(tmp_path.iterdir())


def test_insufficient_disk_reserve_blocks_before_reading_memberships(tmp_path, monkeypatch):
    cfg = plan()
    monkeypatch.setattr(training.shutil, "disk_usage", lambda _: SimpleNamespace(free=6 * 1024**3))

    def unopened():
        raise AssertionError("host refusal must precede membership reads")
        yield

    with pytest.raises(SnapshotError, match="disk_reserve"):
        fit(unopened(), cfg, tmp_path)
    assert not list(tmp_path.iterdir())
