"""Original exposed Source sidecars, complete consumption and bounded full arrays.

These controls verify qualification intake. Source generation/ledger replay and
journal authority are explicitly outside this component's acceptance claim.
"""

import hashlib
import io
import json
import shutil
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

import pytest

from retailops_ai.evaluation_campaign.campaign_stockout_qualification import (
    MANIFEST,
    REPORT,
    WINDOWS,
    CampaignStockoutQualification,
    CampaignStockoutQualificationPolicy,
    _canonical_objects,
)
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256
from retailops_ai.source_snapshot.inventory_protocol import verify_qualification
from retailops_ai.source_snapshot.protocol import Snapshot


def snapshot(raw):
    manifest = json.loads(raw)
    refs = tuple(manifest["metadata_files"]) + tuple(
        f for t in manifest["tables"] for f in t["files"]
    )
    return Snapshot(manifest, refs, hashlib.sha256(raw).hexdigest())


@pytest.fixture(scope="module", params=["inventory", "demand", "physical"])
def original(request, tmp_path_factory):
    root = tmp_path_factory.mktemp("ai09-private-qualification-control")
    name = "inventory-v1_1.zip" if request.param == "inventory" else "anomaly-v1_2.zip"
    public = "facts" if request.param == "inventory" else request.param + "/public"
    private = "private" if request.param == "inventory" else request.param + "/private"
    native = root / "private"
    sidecar = native / "evaluation_truth/qualification"
    sidecar.mkdir(parents=True)
    with ZipFile(Path(__file__).parents[1] / "data/fixtures" / name) as archive:
        for filename in (MANIFEST, REPORT, WINDOWS):
            path = sidecar / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(private + "/evaluation_truth/qualification/" + filename))
        public_snapshot = snapshot(archive.read(public + "/snapshot_manifest.json"))
        private_snapshot = snapshot(archive.read(private + "/snapshot_manifest.json"))
    # Exact legacy qualification verification on the original private sidecar.
    verify_qualification(native, private_snapshot)
    return sidecar, public_snapshot


def reader(case, **kwargs):
    return CampaignStockoutQualification(*case, allow_evaluation_truth=True, **kwargs)


def test_every_original_window_matches_native_sidecar_and_receipt_requires_exit(original):
    root, parent = original
    expected = json.loads((root / WINDOWS).read_bytes())
    component = reader(original)
    with component:
        assert list(component.rows()) == expected
        with pytest.raises(SnapshotError, match="not_completed"):
            component.receipt()
    receipt = component.receipt()
    assert receipt["rows"] == len(expected)
    assert receipt["windows_sha256"] == json_sha256(expected)
    assert receipt["qualification_id"] == parent.manifest["descriptor"]["parent_qualification_id"]
    assert not receipt["ledger_replay_verified"] and not receipt["journal_authorization_proven"]
    assert not receipt["stage_ready"]
    with pytest.raises(SnapshotError, match="single_use"):
        component.__enter__()


def test_partial_or_caught_repeated_stream_never_produces_receipt(original):
    component = reader(original)
    with pytest.raises(SnapshotError, match="complete_stream"):
        with component:
            iterator = component.rows()
            next(iterator)
    with pytest.raises((SnapshotError, ValueError)):
        next(iterator)
    with pytest.raises(SnapshotError, match="not_completed"):
        component.receipt()
    component = reader(original)
    with pytest.raises(SnapshotError, match="complete_stream"):
        with component:
            list(component.rows())
            with pytest.raises(SnapshotError, match="repeated_stream"):
                list(component.rows())
    with pytest.raises(SnapshotError, match="not_completed"):
        component.receipt()


@pytest.mark.parametrize("mutation", ["truncated", "resealed", "extra", "symlink", "report"])
def test_changed_or_resealed_original_parent_is_refused(original, tmp_path, mutation):
    old, parent = original
    root = tmp_path / "qualification"
    shutil.copytree(old, root)
    path = root / WINDOWS
    if mutation == "truncated":
        path.write_bytes(path.read_bytes()[:-10])
    elif mutation == "extra":
        (root / "unexpected").write_bytes(b"x")
    elif mutation == "symlink":
        path.unlink()
        path.symlink_to(old / WINDOWS)
    elif mutation == "report":
        (root / REPORT).write_bytes(b"{}\n")
    else:
        rows = json.loads(path.read_bytes())[:-1]
        path.write_bytes(canonical_json(rows) + b"\n")
        manifest = json.loads((root / MANIFEST).read_bytes())
        manifest["descriptor"].update(rows=len(rows), windows_sha256=json_sha256(rows))
        manifest["qualification_id"] = "inventory-labels-sha256-" + json_sha256(
            manifest["descriptor"]
        )
        manifest["windows"].update(
            size_bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        )
        (root / MANIFEST).write_bytes(canonical_json(manifest) + b"\n")
    with pytest.raises((SnapshotError, OSError)):
        with reader((root, parent)) as component:
            list(component.rows())


def test_original_changed_after_full_read_invalidates_context_and_receipt(original, tmp_path):
    old, parent = original
    root = tmp_path / "qualification"
    shutil.copytree(old, root)
    component = reader((root, parent))
    with pytest.raises(SnapshotError, match="parent_changed"):
        with component:
            list(component.rows())
            (root / REPORT).write_bytes(b"{}\n")
    with pytest.raises(SnapshotError, match="not_completed"):
        component.receipt()


def test_changed_in_memory_identity_cannot_change_the_receipt(original):
    component = reader(original)
    with pytest.raises(SnapshotError, match="metadata_changed"):
        with component:
            list(component.rows())
            component.manifest["qualification_id"] = "inventory-labels-sha256-" + "0" * 64
    with pytest.raises(SnapshotError, match="not_completed"):
        component.receipt()


def test_declared_full_population_budget_never_truncates(original):
    with pytest.raises(SnapshotError, match="parent_mismatch"):
        with reader(original, policy=CampaignStockoutQualificationPolicy(max_windows=1)):
            pytest.fail("oversized parent was admitted")


def test_private_opt_in_precedes_any_path_access(original, tmp_path):
    with pytest.raises(SnapshotError, match="private_opt_in"):
        with CampaignStockoutQualification(tmp_path / "missing", original[1]):
            pytest.fail("private data admitted without explicit opt-in")


@pytest.mark.parametrize("mutation", ["duplicate_UTC", "future_available", "float_outcome"])
def test_record_validation_cannot_be_bypassed_by_controlled_expected_metadata(
    original, tmp_path, mutation
):
    # Explicitly synthetic expected metadata to isolate row validation, not Source proof.
    old, parent = original
    root = tmp_path / "qualification"
    shutil.copytree(old, root)
    rows = json.loads((root / WINDOWS).read_bytes())
    if mutation == "duplicate_UTC":
        row = deepcopy(rows[0])
        row["origin"] = row["origin"].replace("+00:00", "Z")
        rows.insert(1, row)
    else:
        row = next(r for r in rows if r["status"] == "evaluable")
        if mutation == "future_available":
            row["label_available_at"] = "2099-01-01T00:00:00+00:00"
        else:
            row["incident_stockout"] = 0.5
    raw = canonical_json(rows) + b"\n"
    (root / WINDOWS).write_bytes(raw)
    document = json.loads((root / MANIFEST).read_bytes())
    document["descriptor"].update(rows=len(rows), windows_sha256=json_sha256(rows))
    document["qualification_id"] = "inventory-labels-sha256-" + json_sha256(document["descriptor"])
    document["windows"].update(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (root / MANIFEST).write_bytes(canonical_json(document) + b"\n")
    expected = deepcopy(parent.manifest)
    expected["descriptor"]["qualification"] = document["descriptor"]
    expected["descriptor"]["parent_qualification_id"] = document["qualification_id"]
    expected["snapshot_id"] = "snapshot-sha256-" + json_sha256(expected["descriptor"])
    with pytest.raises(SnapshotError, match="chronology|invalid_window"):
        with reader((root, snapshot(canonical_json(expected)))) as component:
            list(component.rows())


def test_streams_more_than_old_metadata_and_membership_limits_without_one_large_read(tmp_path):
    path = tmp_path / "array.json"
    count = 10001
    with path.open("wb") as stream:
        stream.write(b"[")
        for i in range(count):
            if i:
                stream.write(b",")
            stream.write(canonical_json(dict(i=i, payload="\u017c" * 256)))
        stream.write(b"]\n")
    assert path.stat().st_size > 4 * 1024**2
    with path.open("rb") as stream:

        class BoundedReads:
            calls = 0

            def read(self, size):
                assert 0 < size <= 8192
                self.calls += 1
                return stream.read(size)

        bounded = BoundedReads()
        for expected, row in enumerate(
            _canonical_objects(bounded, CampaignStockoutQualificationPolicy())
        ):
            assert row == dict(i=expected, payload="\u017c" * 256)
        assert expected + 1 == count
        assert bounded.calls > 512


@pytest.mark.parametrize("limit", ["record", "windows", "bytes"])
def test_parser_rejects_whole_operation_when_an_explicit_budget_is_exceeded(limit):
    rows = [dict(payload="x" * 1100)] * 5
    values = (
        {"max_record_bytes": 1024}
        if limit == "record"
        else {"max_windows": 1}
        if limit == "windows"
        else {"max_bytes": 4096}
    )
    with pytest.raises(SnapshotError, match="record|window_budget|byte_budget"):
        list(
            _canonical_objects(
                io.BytesIO(canonical_json(rows) + b"\n"),
                CampaignStockoutQualificationPolicy(**values),
            )
        )


@pytest.mark.parametrize("chunk", [1, 7, 64, 8192])
def test_utf8_and_json_escapes_cross_chunk_boundaries(chunk):
    rows = [dict(i=i, text='\u017c\u00f3\u0142w \\" ] , { \n') for i in range(17)]
    policy = CampaignStockoutQualificationPolicy(chunk_bytes=chunk)
    assert list(_canonical_objects(io.BytesIO(canonical_json(rows) + b"\n"), policy)) == rows


@pytest.mark.parametrize(
    "raw",
    [
        b"{}\n",
        b"[{},]\n",
        b"[ {},{}]\n",
        b'[{"x":1,"x":2}]\n',
        b'[{"x":NaN}]\n',
        b"[{}]",
        b"[{}]\nextra",
        b'[{"x":true',
        b"[1]\n",
        b'["x"]\n',
        b'[{"x":"\xff"}]\n',
    ],
)
def test_noncanonical_ambiguous_or_incomplete_arrays_are_refused(raw):
    with pytest.raises(SnapshotError):
        list(
            _canonical_objects(io.BytesIO(raw), CampaignStockoutQualificationPolicy(chunk_bytes=1))
        )
