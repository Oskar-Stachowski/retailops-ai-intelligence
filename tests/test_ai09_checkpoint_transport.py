"""Storage, provenance and cumulative-cost controls; fixtures are not native Source proof."""

import copy
import hashlib
import io
import json
import shutil
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import preparation_attempt as attempt
from retailops_ai.evaluation_campaign import preparation_checkpoint as checkpoints
from retailops_ai.evaluation_campaign import preparation_execution as execution
from retailops_ai.evaluation_campaign import preparation_resume as resume
from retailops_ai.evaluation_campaign import preparation_witness as native
from scripts import ai09_attempt_transport as attempt_transport
from scripts import ai09_checkpoint_transport as transport
from scripts import check_ai09_checkpoint_control as control


def measurement(phase="generation", seconds=1.0):
    return {
        "status": "passed",
        "reason": None,
        "exit_code": 0,
        "wall_seconds": seconds,
        "sampled_worker_cpu_seconds": 0.1,
        "phase": phase,
    }


def fixture_prefix(tmp_path, *, phases=2):
    plan = {
        "controlled_test_only": True,
        "budgets": {
            "tree_rss_bytes": 1024**3,
            "scratch_bytes": 1024**2,
            "wall_seconds": 100,
            "minimum_free_disk_bytes": 1,
            "minimum_available_memory_bytes": 1,
            "sample_seconds": 0.2,
        },
    }
    identity = {
        "scope": checkpoints.SCOPE,
        **{
            name: "a" * (40 if name.endswith("_commit") else 64)
            for name in checkpoints.IDENTITY_FIELDS - {"scope", "validators_sha256"}
        },
        "validators_sha256": {phase: "b" * 64 for phase in checkpoints.PHASES},
    }
    identity["plan_sha256"] = canonical_sha256(plan)
    root = tmp_path / "session"
    execution.initialize(root, plan=plan, identity=identity)
    prior_id = None
    for index, phase in enumerate(checkpoints.PHASES[:phases]):
        data = root / (phase + "-data")
        data.mkdir()
        (data / "report.json").write_text('{"controlled_fixture_only":true}')
        (data / "values.csv").write_text("a,b\n1,2\n")
        result = {checkpoints.RESULT_PATHS[phase]: str(data), "controlled_fixture_only": True}
        witness = native.capture(
            phase,
            data,
            validator_code_sha256=identity["validators_sha256"][phase],
            identity_sha256=canonical_sha256(identity),
            output_id="fixture-" + phase,
            input_ids=["fixture-" + checkpoints.PHASES[i] for i in checkpoints.PARENTS[index]],
            report_paths=["report.json"],
        )
        execution.write_once(root / (phase + ".json"), result)
        execution.write_once(root / (phase + ".witness.json"), witness)
        execution.operate(
            root,
            phase=phase,
            kind="native",
            command=[],
            cwd=root,
            env={},
            roots=(root,),
            monitor=lambda *args, phase=phase, **kwargs: measurement(phase),
            accept=lambda m: None,
        )
        binding = None

        def seal(m, phase=phase, data=data, result=result, witness=witness, prior_id=prior_id):
            nonlocal binding
            actual = execution.inspect(root)["events"][-2]["measurement"]
            _, binding, _ = checkpoints.seal_stage(
                phase,
                data,
                root / "checkpoints" / phase,
                identity=identity,
                result=result,
                measurement=actual,
                witness=witness,
                previous_checkpoint_id=prior_id,
            )
            execution.write_once(root / (phase + ".checkpoint.json"), binding)
            m["checkpoint_binding_sha256"] = canonical_sha256(binding)

        execution.operate(
            root,
            phase=phase,
            kind="seal",
            command=[],
            cwd=root,
            env={},
            roots=(root,),
            monitor=lambda *args, phase=phase, **kwargs: measurement(phase, 0.5),
            accept=seal,
        )
        prior_id = binding["checkpoint_id"]
    return root, identity


def zip_bundle(bundle, archive, *, extra=None):
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                zipped.write(path, path.relative_to(bundle).as_posix())
        if extra:
            zipped.writestr(*extra)
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def restored_prefix(tmp_path, *, phases=2):
    original, identity = fixture_prefix(tmp_path, phases=phases)
    bundle = tmp_path / "bundle"
    manifest = transport.bundle_prefix(original, bundle)
    archive = tmp_path / "download.zip"
    digest = zip_bundle(bundle, archive)
    downloaded = tmp_path / "downloaded"
    transport.import_zip(
        archive, downloaded, expected_digest=digest, identity=identity, maximum=1024**2
    )
    bindings = []
    for phase in checkpoints.PHASES[:phases]:
        binding = json.loads((downloaded / (phase + ".checkpoint.json")).read_bytes())
        bindings.append((downloaded / "checkpoints" / phase / binding["checkpoint_id"], binding))
    restored = checkpoints.restore_chain(bindings, tmp_path / "restored", identity=identity)
    receipt = {
        "verified_github_artifact": True,
        "terminal_attempt_history_required": False,
        "run_id": 17,
        "deliberate_prefix_stop": True,
        "event_sha256": manifest["event_sha256"],
        "identity_sha256": canonical_sha256(identity),
    }
    retained = resume.retain_prefix_archives(downloaded, tmp_path / "retained-checkpoints")
    return original, identity, downloaded, restored, receipt, retained


def failed_history(original, previous, identity, *, outcome="native_failed"):
    next_phase = checkpoints.PHASES[execution.inspect(previous)["completed_phases"]]

    def operate(kind, failed=False, unknown=False):
        measured = measurement(next_phase, seconds=2)
        if failed:
            measured.update(status="failed", reason="controlled_failure", exit_code=7)
        if unknown:
            measured["sampled_worker_cpu_seconds"] = None
        execution.operate(
            original,
            phase=next_phase,
            kind=kind,
            command=[],
            cwd=original,
            env={},
            roots=(original,),
            monitor=lambda *a, **kw: measured,
            accept=lambda m: None,
        )

    if outcome == "actual_process":
        execution.operate(
            original,
            phase=next_phase,
            kind="native",
            command=[
                sys.executable,
                "-I",
                "-c",
                "import time; time.sleep(0.05); raise SystemExit(23)",
            ],
            cwd=original,
            env={},
            roots=(original,),
            monitor=control.probe_module().monitor,
            accept=lambda m: pytest.fail("failed process cannot be accepted"),
        )
    elif outcome in {"seal_failed", "interrupted_seal"}:
        operate("native")
    if outcome.startswith("interrupted"):
        state = execution.inspect(original)
        execution.write_once(
            original / "events" / f"{len(state['events']):03d}.json",
            {
                "sequence": len(state["events"]),
                "event": "started",
                "previous_sha256": canonical_sha256(state["events"][-1]),
                "phase": next_phase,
                "kind": "seal" if outcome.endswith("seal") else "native",
                "remaining_wall_seconds": state["remaining_wall_seconds"],
                "at_utc": "controlled_interruption_boundary",
            },
        )
    elif outcome != "actual_process":
        operate("seal" if outcome == "seal_failed" else "native", True, outcome == "unknown_cpu")
    captured = attempt.snapshot(original)
    head = identity["consumer_commit"]
    remote = {
        "authenticated_github_download": True,
        "snapshot_sha256": canonical_sha256(captured),
        "downloaded_zip_sha256": "d" * 64,
        "run": {
            "id": 17,
            "run_attempt": 1,
            "repository": {"full_name": attempt.REPOSITORY},
            "head_sha": head,
            "status": "completed",
            "conclusion": "failure",
            "event": "workflow_dispatch",
            "path": ".github/workflows/ai09-generation-control.yml",
        },
        "job": {
            "id": 23,
            "name": "control",
            "run_id": 17,
            "run_attempt": 1,
            "head_sha": head,
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-10-09T00:00:00Z",
            "completed_at": "2026-10-09T00:00:20Z",
        },
        "artifact": {
            "id": 31,
            "workflow_run": {"id": 17, "head_sha": head},
            "name": f"ai09-preparation-attempt-{head}-17-1",
            "expired": False,
            "digest": "sha256:" + "d" * 64,
            "created_at": "2026-10-09T00:00:19Z",
        },
    }
    return attempt.bind_history(previous, captured=captured, authenticated_remote=remote)


@pytest.mark.parametrize("phases", [1, 2, 3, 4])
@pytest.mark.parametrize(
    "outcome",
    [
        "native_failed",
        "seal_failed",
        "interrupted_native",
        "interrupted_seal",
        "unknown_cpu",
        "actual_process",
    ],
)
def test_resume_preserves_failed_work_and_reserves_interruption_without_inventing_cpu(
    tmp_path, outcome, phases
):
    original, identity, previous, restored, receipt, retained = restored_prefix(
        tmp_path, phases=phases
    )
    before = execution.inspect(previous)
    history = failed_history(original, previous, identity, outcome=outcome)
    failed = execution.inspect(original)
    if outcome == "actual_process":
        assert failed["events"][-1]["measurement"]["exit_code"] == 23
        assert failed["events"][-1]["charged_wall_seconds"] > 0.05
    original_bytes = {p.name: p.read_bytes() for p in (original / "events").glob("*.json")}
    output = tmp_path / "resumed-after-failure"
    resume.resume_prefix(
        previous,
        output,
        identity=identity,
        expected_event_sha256=receipt["event_sha256"],
        restored=restored,
        transport_receipt=receipt,
        transport_measurement=measurement(seconds=2),
        restore_measurement=measurement(seconds=3),
        retained_archives=retained,
        attempt_history=history,
    )
    state = execution.inspect(output)
    reserve = 22 if outcome.startswith("interrupted") else 0
    known = sum(
        e["charged_wall_seconds"]
        for e in failed["events"][len(before["events"]) :]
        if e["event"] == "finished"
    )
    assert state["charged_wall_seconds"] == pytest.approx(
        before["charged_wall_seconds"] + known + reserve + 5
    )
    assert state["remaining_wall_seconds"] == pytest.approx(100 - state["charged_wall_seconds"])
    assert state["reserved_unknown_wall_seconds"] == reserve
    assert state["historical_unmeasured_wall_cost_present"] == bool(reserve)
    assert state["unmeasured_wall_cost_present"] == bool(reserve)
    assert state["unsettled_wall_cost_present"] is False
    assert state["unmeasured_cpu_cost_present"] == (bool(reserve) or outcome == "unknown_cpu")
    assert state["worker_cpu_seconds_lower_bound"] == pytest.approx(
        failed["worker_cpu_seconds_lower_bound"] + 0.2
    )
    assert state["events"] == before["events"]
    assert original_bytes == {p.name: p.read_bytes() for p in (original / "events").glob("*.json")}
    saved = json.loads(next((output / "resume-receipts").glob("*.json")).read_bytes())
    assert saved["attempt_history"]["snapshot"]["events"] == failed["events"]
    # A later deliberate pause must not lose the failure or turn unknown CPU into zero.
    manifest = transport.bundle_prefix(output, tmp_path / "after-failure-bundle")
    again = tmp_path / "resumed-again"
    resume.resume_prefix(
        output,
        again,
        identity=identity,
        expected_event_sha256=manifest["event_sha256"],
        restored=restored,
        transport_receipt={**receipt, "event_sha256": manifest["event_sha256"]},
        transport_measurement=measurement(seconds=1),
        restore_measurement=measurement(seconds=1),
        retained_archives=resume.retain_prefix_archives(output, tmp_path / "second-retained"),
    )
    after = execution.inspect(again)
    assert after["charged_wall_seconds"] == state["charged_wall_seconds"] + 2
    assert after["reserved_unknown_wall_seconds"] == reserve
    assert after["unmeasured_cpu_cost_present"] == state["unmeasured_cpu_cost_present"]
    assert len(after["cost_adjustments"]) == 2
    assert len(list((again / "resume-receipts").glob("*.json"))) == 2


@pytest.mark.parametrize(
    "damage",
    [
        "running",
        "successful",
        "old_job_attempt",
        "other_repository",
        "other_head",
        "old_artifact",
        "wrong_name",
        "expired",
        "digest",
        "snapshot_digest",
        "missing_suffix",
        "rewritten_prefix",
        "forgotten_adjustment",
        "budget",
        "untrusted",
        "wrong_job",
        "non_dispatch",
        "remaining_budget_reset",
        "lost_native_finish",
        "foreign_prefix_run",
    ],
)
def test_failed_history_admission_rejects_old_partial_or_unbound_evidence(tmp_path, damage):
    original, identity, previous, restored, receipt, retained = restored_prefix(tmp_path)
    history = failed_history(original, previous, identity, outcome="interrupted_seal")
    remote, captured = history["remote"], history["snapshot"]
    if damage == "running":
        remote["run"]["status"] = "in_progress"
    elif damage == "successful":
        remote["job"]["conclusion"] = "success"
    elif damage == "old_job_attempt":
        remote["run"]["run_attempt"] = 2
    elif damage == "other_repository":
        remote["run"]["repository"]["full_name"] = "other/repo"
    elif damage == "other_head":
        remote["job"]["head_sha"] = "c" * 40
    elif damage == "old_artifact":
        remote["artifact"]["created_at"] = "2026-10-08T23:59:59Z"
    elif damage == "wrong_name":
        remote["artifact"]["name"] += "-older-prefix"
    elif damage == "expired":
        remote["artifact"]["expired"] = True
    elif damage == "digest":
        remote["artifact"]["digest"] = "sha256:" + "c" * 64
    elif damage == "snapshot_digest":
        remote["snapshot_sha256"] = "c" * 64
    elif damage == "missing_suffix":
        captured["events"] = captured["events"][:8]
    elif damage == "rewritten_prefix":
        captured["events"][0]["at_utc"] = "changed"
    elif damage == "forgotten_adjustment":
        captured["execution"]["cost_adjustments"] = [{"lost": True}]
    elif damage == "budget":
        remote["job"]["completed_at"] = "2026-10-09T00:10:00Z"
    elif damage == "wrong_job":
        remote["job"]["name"] = "other_failed_job"
    elif damage == "non_dispatch":
        remote["run"]["event"] = "pull_request"
    elif damage == "remaining_budget_reset":
        captured["events"][-1]["remaining_wall_seconds"] = 100
        remote["snapshot_sha256"] = canonical_sha256(captured)
    elif damage == "lost_native_finish":
        del captured["events"][-2]
        remote["snapshot_sha256"] = canonical_sha256(captured)
    elif damage == "foreign_prefix_run":
        receipt["run_id"] = 99
    else:
        remote["authenticated_github_download"] = False
    with pytest.raises(ValueError):
        resume.resume_prefix(
            previous,
            tmp_path / "rejected",
            identity=identity,
            expected_event_sha256=receipt["event_sha256"],
            restored=restored,
            transport_receipt=receipt,
            transport_measurement=measurement(),
            restore_measurement=measurement(),
            retained_archives=retained,
            attempt_history=history,
        )
    assert not (tmp_path / "rejected").exists()
    assert Path(retained["directory"]).is_dir()
    assert execution.inspect(original)["status"] == "unfinished"


@pytest.mark.parametrize(
    "damage", [None, "live_run", "rerun_during_download", "extra_member", "link", "size"]
)
def test_terminal_attempt_transport_binds_actual_archive_and_latest_run(
    tmp_path, monkeypatch, damage
):
    import download_forecast_cohort_checkpoint as legacy

    original, identity, previous, _, _, _ = restored_prefix(tmp_path)
    history = failed_history(original, previous, identity)
    remote = copy.deepcopy(history["remote"])
    archive = tmp_path / "terminal.zip"
    with zipfile.ZipFile(archive, "x") as zipped:
        info = zipfile.ZipInfo("attempt.json")
        if damage == "link":
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(info, json.dumps(history["snapshot"]))
        if damage == "extra_member":
            zipped.writestr("unaccounted.json", "{}")
    metadata = remote["artifact"]
    metadata.update(
        url=legacy.API + "31",
        archive_download_url=legacy.API + "31/zip",
        size_in_bytes=archive.stat().st_size,
        digest="sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
    )
    if damage == "size":
        metadata["size_in_bytes"] += 1
    requests, transfers = [], []

    def metadata_read(kind, identifier, token):
        requests.append((kind, identifier))
        answer = copy.deepcopy(remote["run" if kind == "runs" else "job"])
        if kind == "runs" and damage == "live_run":
            answer["status"] = "in_progress"
        if kind == "runs" and damage == "rerun_during_download" and len(requests) > 2:
            answer["run_attempt"] = 2
        return answer

    def download(identifier, target, digest, maximum, token):
        transfers.append(identifier)
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest
        assert archive.stat().st_size <= maximum
        shutil.copyfile(archive, target)

    monkeypatch.setattr(attempt_transport, "github_metadata", metadata_read)
    monkeypatch.setattr(legacy, "artifact_metadata", lambda identifier, token: metadata)
    monkeypatch.setattr(legacy, "_download_zip", download)
    output = tmp_path / "retrieved-attempt"

    def call():
        return attempt_transport.retrieve_history(
            previous,
            output,
            artifact_id=31,
            run_id=17,
            job_id=23,
            identity=identity,
            token=None,
        )

    if damage:
        with pytest.raises(ValueError):
            call()
        assert not (output / "verified-history.json").exists()
        if damage == "live_run":
            assert not transfers
    else:
        result = call()
        assert result["snapshot"] == history["snapshot"]
        assert result["remote"]["downloaded_zip_sha256"] == metadata["digest"][7:]
        assert requests == [("runs", 17), ("jobs", 23), ("runs", 17)]
        assert json.loads((output / "verified-history.json").read_bytes()) == result
        assert (output / "artifact.zip").read_bytes() == archive.read_bytes()
        assert stat.S_IMODE((output / "artifact.zip").stat().st_mode) == 0o600


@pytest.mark.parametrize("context", ["same_live_run", "other_run", "other_head"])
def test_prefix_retrieval_requires_terminal_history_outside_its_current_run(
    tmp_path, monkeypatch, context
):
    import download_forecast_cohort_checkpoint as legacy

    original, identity = fixture_prefix(tmp_path)
    bundle = tmp_path / "portable"
    transport.bundle_prefix(original, bundle)
    archive = tmp_path / "prefix.zip"
    digest = zip_bundle(bundle, archive)
    head = identity["consumer_commit"]
    metadata = {
        "id": 31,
        "name": "selected-prefix",
        "url": legacy.API + "31",
        "archive_download_url": legacy.API + "31/zip",
        "workflow_run": {"id": 17, "head_sha": head},
        "expired": False,
        "digest": "sha256:" + digest,
        "size_in_bytes": archive.stat().st_size,
    }
    monkeypatch.setenv("GITHUB_RUN_ID", "18" if context == "other_run" else "17")
    monkeypatch.setenv("GITHUB_SHA", "c" * 40 if context == "other_head" else head)
    monkeypatch.setattr(legacy, "artifact_metadata", lambda identifier, token: metadata)
    monkeypatch.setattr(
        legacy, "_download_zip", lambda identifier, target, *a: shutil.copyfile(archive, target)
    )
    receipt = transport.retrieve(
        artifact_id=31,
        run_id=17,
        head=head,
        name="selected-prefix",
        identity=identity,
        output=tmp_path / "retrieved-prefix",
        token=None,
        maximum=1024**2,
    )
    assert receipt["terminal_attempt_history_required"] == (context != "same_live_run")


@pytest.mark.parametrize(
    "kind,identifier", [("runs/17/../../other", 1), ("jobs", True), ("runs", -1)]
)
def test_terminal_metadata_rejects_routes_before_network(monkeypatch, kind, identifier):
    monkeypatch.setattr(
        attempt_transport.http.client, "HTTPSConnection", lambda *a, **kw: pytest.fail("network")
    )
    with pytest.raises(ValueError, match="metadata_route"):
        attempt_transport.github_metadata(kind, identifier, None)


@pytest.mark.parametrize("status", [200, 302])
def test_terminal_metadata_credentials_go_only_to_fixed_api_and_never_redirect(monkeypatch, status):
    calls = []

    class Response(io.BytesIO):
        pass

    response = Response(b'{"id":17}')
    response.status = status

    class Connection:
        def __init__(self, host, **kwargs):
            calls.append(("host", host))

        def request(self, method, target, headers):
            calls.append((method, target, headers))

        def getresponse(self):
            return response

        def close(self):
            calls.append(("closed",))

    monkeypatch.setattr(attempt_transport.http.client, "HTTPSConnection", Connection)
    if status == 200:
        assert attempt_transport.github_metadata("runs", 17, "test-only-not-a-secret") == {"id": 17}
    else:
        with pytest.raises(ValueError, match="metadata_http_302"):
            attempt_transport.github_metadata("runs", 17, "test-only-not-a-secret")
    assert calls[0] == ("host", "api.github.com")
    assert calls[1][0:2] == ("GET", "/repos/" + attempt.REPOSITORY + "/actions/runs/17")
    assert calls[1][2]["Authorization"] == "Bearer test-only-not-a-secret"
    assert calls[-1] == ("closed",)
    assert response.closed


def test_downloaded_checkpoint_prefix_resumes_with_original_cost_plus_transport_and_restore(
    tmp_path,
):
    original, identity, downloaded, restored, receipt, retained = restored_prefix(tmp_path)
    output = tmp_path / "resumed"
    resume.resume_prefix(
        downloaded,
        output,
        identity=identity,
        expected_event_sha256=receipt["event_sha256"],
        restored=restored,
        transport_receipt=receipt,
        transport_measurement=measurement(seconds=2),
        restore_measurement=measurement(seconds=3),
        retained_archives=retained,
    )
    before, after = execution.inspect(original), execution.inspect(output)
    assert after["events"] == before["events"]
    assert after["charged_wall_seconds"] == before["charged_wall_seconds"] + 5
    assert after["remaining_wall_seconds"] == before["remaining_wall_seconds"] - 5
    assert after["worker_cpu_seconds_lower_bound"] == before["worker_cpu_seconds_lower_bound"] + 0.2
    # Actual next process observes the reduced budget, not the original100-second budget.
    observed = []

    def monitor(*args, **kwargs):
        observed.append(execution.inspect(output)["events"][-1]["remaining_wall_seconds"])
        return measurement("export")

    execution.operate(
        output,
        phase="export",
        kind="native",
        command=[],
        cwd=output,
        env={},
        roots=(output,),
        monitor=monitor,
        accept=lambda m: None,
    )
    assert observed == [after["remaining_wall_seconds"]]
    for phase in checkpoints.PHASES[:2]:
        original_result = json.loads((original / (phase + ".json")).read_bytes())
        result = json.loads((output / (phase + ".json")).read_bytes())
        path = checkpoints.RESULT_PATHS[phase]
        assert result[path] != original_result[path]
        assert native.inventory(Path(result[path])) == native.inventory(Path(original_result[path]))


@pytest.mark.parametrize(
    "damage",
    [
        "unknown_cpu",
        "failed",
        "budget",
        "identity",
        "history",
        "transport",
        "result_rewrite",
        "missing_terminal_history",
    ],
)
def test_resume_rejects_untrusted_history_unknown_cost_and_changed_results(tmp_path, damage):
    _, identity, downloaded, restored, receipt, retained = restored_prefix(tmp_path)
    cost = measurement(seconds=2)
    expected = receipt["event_sha256"]
    if damage == "unknown_cpu":
        cost["sampled_worker_cpu_seconds"] = None
    elif damage == "failed":
        cost["status"] = "failed"
    elif damage == "budget":
        cost["wall_seconds"] = 1000
    elif damage == "identity":
        identity = {**identity, "consumer_commit": "c" * 40}
    elif damage == "history":
        expected = "c" * 64
    elif damage == "transport":
        receipt["verified_github_artifact"] = False
    elif damage == "missing_terminal_history":
        receipt["terminal_attempt_history_required"] = True
    else:
        restored[0]["resumed_result"]["controlled_fixture_only"] = False
    with pytest.raises(ValueError):
        resume.resume_prefix(
            downloaded,
            tmp_path / "resumed",
            identity=identity,
            expected_event_sha256=expected,
            restored=restored,
            transport_receipt=receipt,
            transport_measurement=cost,
            restore_measurement=measurement(),
            retained_archives=retained,
        )
    assert not (tmp_path / "resumed").exists()


def test_resume_cost_receipt_cannot_be_removed_or_budget_reset_after_publication(tmp_path):
    _, identity, downloaded, restored, receipt, retained = restored_prefix(tmp_path)
    output = tmp_path / "resumed"
    resume.resume_prefix(
        downloaded,
        output,
        identity=identity,
        expected_event_sha256=receipt["event_sha256"],
        restored=restored,
        transport_receipt=receipt,
        transport_measurement=measurement(),
        restore_measurement=measurement(),
        retained_archives=retained,
    )
    path = next((output / "resume-receipts").glob("*.json"))
    data = json.loads(path.read_bytes())
    data["transport_measurement"]["wall_seconds"] = 0
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="resume_cost_receipt_mismatch"):
        execution.inspect(output)


def test_two_resumes_keep_original_archives_all_receipts_and_cumulative_cost(tmp_path):
    original, identity, previous, restored, receipt, retained = restored_prefix(tmp_path)
    before = execution.inspect(original)
    for number in (1, 2):
        output = tmp_path / f"resumed-{number}"
        resume.resume_prefix(
            previous,
            output,
            identity=identity,
            expected_event_sha256=receipt["event_sha256"],
            restored=restored,
            transport_receipt=receipt,
            transport_measurement=measurement(seconds=2),
            restore_measurement=measurement(seconds=3),
            retained_archives=retained,
        )
        after = execution.inspect(output)
        assert after["events"] == before["events"]
        assert len(after["cost_adjustments"]) == number
        assert after["charged_wall_seconds"] == before["charged_wall_seconds"] + 5 * number
        assert after["remaining_wall_seconds"] == before["remaining_wall_seconds"] - 5 * number
        assert after["worker_cpu_seconds_lower_bound"] == pytest.approx(
            before["worker_cpu_seconds_lower_bound"] + 0.2 * number
        )
        assert not Path(retained["directory"]).exists()  # Atomic transfer, no second copy.
        for path in (output / "checkpoints").rglob("*"):
            relative = path.relative_to(output)
            if path.is_file():
                assert path.read_bytes() == (original / relative).read_bytes()
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
            else:
                assert stat.S_IMODE(path.stat().st_mode) == 0o700
        # The first resumed session must itself produce a complete portable prefix.
        bundle = tmp_path / f"bundle-{number}"
        manifest = transport.bundle_prefix(output, bundle)
        archive = tmp_path / f"download-{number}.zip"
        digest = zip_bundle(bundle, archive)
        previous = tmp_path / f"downloaded-{number}"
        transport.import_zip(
            archive, previous, expected_digest=digest, identity=identity, maximum=1024**2
        )
        receipt = {**receipt, "event_sha256": manifest["event_sha256"]}
        bindings = []
        for phase in checkpoints.PHASES[:2]:
            binding = json.loads((previous / (phase + ".checkpoint.json")).read_bytes())
            bindings.append((previous / "checkpoints" / phase / binding["checkpoint_id"], binding))
        restored = checkpoints.restore_chain(
            bindings, tmp_path / f"restored-{number}", identity=identity
        )
        retained = resume.retain_prefix_archives(previous, tmp_path / f"retained-{number}")


@pytest.mark.parametrize("damage", ["changed", "missing", "extra", "symlink", "identity"])
def test_changed_retained_archive_cannot_publish_resumed_session(tmp_path, damage):
    original, identity, previous, restored, receipt, retained = restored_prefix(tmp_path)
    root = Path(retained["directory"])
    payload = next(root.rglob("payload.tar.gz"))
    original_bytes = payload.read_bytes()
    if damage == "changed":
        payload.write_bytes(original_bytes + b"changed after guarded verification")
    elif damage == "missing":
        payload.unlink()
    elif damage == "extra":
        (root / "unexpected").mkdir()
    elif damage == "symlink":
        payload.unlink()
        payload.symlink_to(original / "checkpoints" / payload.relative_to(root))
    else:
        retained["identity_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="(archive_handoff|retained_archive)"):
        resume.resume_prefix(
            previous,
            tmp_path / "resumed",
            identity=identity,
            expected_event_sha256=receipt["event_sha256"],
            restored=restored,
            transport_receipt=receipt,
            transport_measurement=measurement(),
            restore_measurement=measurement(),
            retained_archives=retained,
        )
    assert not (tmp_path / "resumed").exists()
    assert (original / "checkpoints" / payload.relative_to(root)).read_bytes() == original_bytes


@pytest.mark.parametrize(
    "unsafe", ["../escape", "/absolute", "checkpoints/../escape", "extra.json", "symlink"]
)
def test_transport_rejects_extra_paths_traversal_and_links_without_exposing_output(
    tmp_path, unsafe
):
    root, identity = fixture_prefix(tmp_path)
    bundle = tmp_path / "bundle"
    transport.bundle_prefix(root, bundle)
    entry = unsafe
    if unsafe == "symlink":
        entry = zipfile.ZipInfo("link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive = tmp_path / "input.zip"
    digest = zip_bundle(bundle, archive, extra=(entry, "outside"))
    with pytest.raises((ValueError, OSError)):
        transport.import_zip(
            archive,
            tmp_path / "imported",
            expected_digest=digest,
            identity=identity,
            maximum=1024**2,
        )
    assert not (tmp_path / "imported").exists()
    assert not (tmp_path / "escape").exists()


def test_wrong_outer_digest_or_native_identity_cannot_import_even_valid_inner_archives(tmp_path):
    root, identity = fixture_prefix(tmp_path)
    bundle = tmp_path / "bundle"
    transport.bundle_prefix(root, bundle)
    archive = tmp_path / "input.zip"
    digest = zip_bundle(bundle, archive)
    for altered_digest, altered_identity in [
        ("f" * 64, identity),
        (digest, {**identity, "producer_commit": "d" * 40}),
    ]:
        with pytest.raises(ValueError):
            transport.import_zip(
                archive,
                tmp_path / "imported",
                expected_digest=altered_digest,
                identity=altered_identity,
                maximum=1024**2,
            )
        assert not (tmp_path / "imported").exists()


@pytest.mark.parametrize(
    "field",
    ["id", "name", "expired", "digest", "size_in_bytes", "workflow_run", "archive_download_url"],
)
def test_github_artifact_metadata_must_match_selected_exact_run_head_and_digest(field):
    metadata = {
        "id": 12,
        "name": "controlled-prefix",
        "expired": False,
        "digest": "sha256:" + "d" * 64,
        "size_in_bytes": 123,
        "workflow_run": {"id": 34, "head_sha": "a" * 40},
        "url": "https://api.github.com/repos/Oskar-Stachowski/retailops-ai-intelligence/actions/artifacts/12",
        "archive_download_url": "https://api.github.com/repos/Oskar-Stachowski/retailops-ai-intelligence/actions/artifacts/12/zip",
    }
    expected = dict(
        artifact_id=12, run_id=34, head="a" * 40, name="controlled-prefix", maximum=1024
    )
    assert transport.validate_metadata(metadata, **expected) == "d" * 64
    altered = copy.deepcopy(metadata)
    altered[field] = {"id": 34, "head_sha": "b" * 40} if field == "workflow_run" else None
    with pytest.raises(ValueError, match="untrusted_artifact_identity"):
        transport.validate_metadata(altered, **expected)


def test_fixed_control_worker_cannot_be_reused_for_full_or_final_generation(tmp_path, monkeypatch):
    probe = control.probe_module()
    monkeypatch.setattr(probe, "require_remote", lambda: None)
    altered = control.control_plan()
    altered["generation"]["products"] = 100
    (tmp_path / "preparation-plan.json").write_text(json.dumps(altered))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "control",
            "--source",
            str(tmp_path / "missing-source"),
            "--output",
            str(tmp_path),
            "--operation",
            "native",
            "--phase",
            "generation",
        ],
    )
    with pytest.raises(ValueError, match="exact_tiny_plan_required"):
        control.main()
    assert not (tmp_path / "missing-source").exists()


def test_github_credentials_are_not_inherited_by_native_or_sealer_children(tmp_path, monkeypatch):
    keys = ("GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    for key in keys:
        monkeypatch.setenv(key, "controlled-placeholder")
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    environment = control.consumer().environment(tmp_path)
    assert all(key not in environment for key in keys)
    assert environment["PYTHONHASHSEED"] == "0"
