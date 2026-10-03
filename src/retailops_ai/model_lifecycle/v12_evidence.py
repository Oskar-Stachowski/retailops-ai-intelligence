"""Read v12 evidence with its pinned offline verifier, without importing AI 04 here."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.source_snapshot.files import (
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    read_json,
    relative_path,
)

VERSION = "forecast-functional-v12-run-1.0.0"
MAX_BYTES = 64 * 1024**3
MAX_FILES = 20000
REPORTS = (
    "run_manifest.json",
    "handoff.json",
    "model_card.json",
    "signature.json",
    "input_example.json",
    "campaign/freeze.json",
    "campaign/campaign_manifest.json",
    "campaign/metrics.json",
    "replay/replay_receipt.json",
)


class V12ArtifactReceipt(Contract):
    """Campaign artifacts use AI 04's byte budget, including empty retained files."""

    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0, le=MAX_BYTES)]


# The operator selects an installed, reviewed AI 04 wheel, never code from the export.
# No scoring, fitting, source generation, registry or exposure operations occur here.
VERIFIER = r"""
import hashlib,json,sys
from pathlib import Path
import retailops_ai
from retailops_ai.forecasting.functional_v12_run import verify_run
package=Path(retailops_ai.__file__).resolve()
site=Path(sys.prefix).resolve()/"lib"/("python%d.%d" % sys.version_info[:2])/"site-packages"
if not package.is_relative_to(site):
    raise RuntimeError("installed_verifier_wheel_required")
root=Path(sys.argv[1])
manifest=verify_run(root)
print(json.dumps({"status":"passed","run_id":manifest["run_id"],
    "run_manifest_sha256":hashlib.sha256((root/"run_manifest.json").read_bytes()).hexdigest(),
    "verifier_code_sha256":manifest["descriptor"]["code"]["code_sha256"],
    "package_file":str(package),"source_generation":False,"model_refits":0},sort_keys=True))
"""


@dataclass(frozen=True)
class V12Evidence:
    root: Path
    manifest: dict[str, Any]
    manifest_receipt: V12ArtifactReceipt
    handoff: dict[str, Any]
    freeze: dict[str, Any]
    metrics: dict[str, Any]
    signature: dict[str, Any]
    card: dict[str, Any]
    verifier: dict[str, Any]

    @property
    def run_id(self) -> str:
        return str(self.manifest["run_id"])

    @property
    def files(self) -> dict[str, V12ArtifactReceipt]:
        result = {
            name: V12ArtifactReceipt.model_validate(receipt)
            for name, receipt in self.manifest["descriptor"]["files"].items()
        }
        result["run_manifest.json"] = self.manifest_receipt
        return result

    def verify_bytes(self) -> None:
        if read_json(self.root, "run_manifest.json") != self.manifest:
            raise ValueError("v12_import_export_changed")
        for name, expected in (
            ("handoff.json", self.handoff),
            ("campaign/freeze.json", self.freeze),
            ("campaign/metrics.json", self.metrics),
            ("signature.json", self.signature),
            ("model_card.json", self.card),
        ):
            if read_json(self.root, name) != expected:
                raise ValueError("v12_import_export_changed")
        inventory(self.root, set(self.files))
        for name, receipt in self.files.items():
            if file_hash(self.root, name) != (receipt.size_bytes, receipt.sha256):
                raise ValueError("v12_import_export_changed")


def verify_with_wheel(root: Path, python: Path, timeout_seconds: int = 3600) -> dict[str, Any]:
    """A credential-free, isolated process uses AI 04's complete byte/semantic verifier."""
    if not python.is_absolute() or not python.is_file() or not 1 <= timeout_seconds <= 7200:
        raise ValueError("v12_import_verifier_configuration")
    with tempfile.TemporaryDirectory(prefix="ai05-v12-verify-") as temporary:
        work = Path(temporary).resolve()
        with (work / "stdout.json").open("xb") as out, (work / "stderr.log").open("xb") as err:
            completed = subprocess.run(  # noqa: S603 - explicit operator-selected interpreter
                [str(python), "-I", "-B", "-c", VERIFIER, str(root)],
                cwd=work,
                env={"PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(work)},
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                timeout=timeout_seconds,
                check=False,
            )
        if completed.returncode:
            raise ValueError("v12_import_pinned_verifier_failed")
        return decode_json((work / "stdout.json").read_bytes())


def load_evidence(root: Path, python: Path, *, timeout_seconds: int = 3600) -> V12Evidence:
    root = checked_directory(root)
    manifest = read_json(root, "run_manifest.json")
    descriptor = manifest["descriptor"]
    if (
        descriptor["version"] != VERSION
        or manifest["run_id"] != "functional-v12-run-sha256-" + canonical_sha256(descriptor)
        or not 1 <= len(descriptor["checkpoints"]) <= 64
        or not 1 <= len(descriptor["files"]) <= MAX_FILES
        or type(descriptor["bytes"]) is not int
        or not 0 <= descriptor["bytes"] <= MAX_BYTES
    ):
        raise ValueError("v12_import_identity_or_budget")
    files = descriptor["files"]
    if "run_manifest.json" in files or not set(REPORTS[1:]).issubset(files):
        raise ValueError("v12_import_missing_reports")
    for name, ref in files.items():
        relative_path(name)
        V12ArtifactReceipt.model_validate(ref)
    if sum(ref["size_bytes"] for ref in files.values()) != descriptor["bytes"]:
        raise ValueError("v12_import_byte_inventory")
    inventory(root, {"run_manifest.json", *files})
    size, digest = file_hash(root, "run_manifest.json")
    verifier = verify_with_wheel(root, python, timeout_seconds)
    if (
        verifier["status"] != "passed"
        or verifier["run_id"] != manifest["run_id"]
        or verifier["run_manifest_sha256"] != digest
        or verifier["verifier_code_sha256"] != descriptor["code"]["code_sha256"]
        or verifier["source_generation"] is not False
        or verifier["model_refits"] != 0
    ):
        raise ValueError("v12_import_verifier_binding")
    evidence = V12Evidence(
        root=root,
        manifest=manifest,
        manifest_receipt=V12ArtifactReceipt(size_bytes=size, sha256=digest),
        handoff=read_json(root, "handoff.json"),
        freeze=read_json(root, "campaign/freeze.json"),
        metrics=read_json(root, "campaign/metrics.json"),
        signature=read_json(root, "signature.json"),
        card=read_json(root, "model_card.json"),
        verifier=verifier,
    )
    if (
        evidence.handoff["deployment"] != "not_promoted"
        or evidence.handoff["mlflow_or_registry_written"] is not False
        or evidence.handoff["ai05_import"]["legacy_single_point_import_compatible"] is not False
        or evidence.handoff["independent_replay"] != "passed"
        or evidence.handoff["all_preregistered_cohorts_included"] is not True
        or evidence.signature["deployable_service_contract"] is not False
        or evidence.signature["version"] != "forecast-functional-v12-signature-1.0.0"
        or set(evidence.signature["outputs"]) != {"candidate", "baseline", "metadata"}
        or set(evidence.signature["outputs"]["candidate"]) != {"median", "mean", "interval"}
        or not isinstance(evidence.signature["outputs"]["baseline"], str)
        or not evidence.signature["outputs"]["baseline"]
        or not isinstance(evidence.signature["outputs"]["metadata"], str)
        or not evidence.signature["outputs"]["metadata"]
        or set(evidence.signature["output_schema"]["properties"]) != {"median", "mean", "interval"}
        or set(evidence.signature["output_schema"]["required"]) != {"median", "mean", "interval"}
        or evidence.signature["output_schema_scope"]
        != "each_of_candidate_and_baseline_in_returned_three_tuple"
        or evidence.signature["return_tuple"]
        != ["candidate: FunctionalForecast", "baseline: FunctionalForecast", "metadata: dict"]
        or any(
            evidence.handoff[name] != descriptor[name]
            for name in (
                "campaign_id",
                "freeze_id",
                "replay_id",
                "forecast_model_status",
                "quality_qualification_status",
            )
        )
        or evidence.metrics["status"] != descriptor["quality_qualification_status"]
        or evidence.metrics["status"] not in {"passed", "not_ready"}
        or descriptor["forecast_model_status"]
        != ("ready" if evidence.metrics["status"] == "passed" else "not_ready")
    ):
        raise ValueError("v12_import_handoff_or_dual_target_binding")
    # Also detects a mutation between the child verifier and metadata projection.
    evidence.verify_bytes()
    return evidence


def projected_metrics(evidence: V12Evidence) -> dict[str, float]:
    """Keep the functional and reference in every metric name; nulls remain in reports."""
    result = {
        "segments_" + key: float(value) for key, value in evidence.metrics["segment_counts"].items()
    }
    for segment in evidence.metrics["segments"]:
        if segment["dimension"] != "global":
            continue
        prefix = str(segment["fold"]) + "." + str(segment["role"])
        for side in ("candidate", "baseline"):
            for component, names in {
                "median": ("mae", "normalized_bias"),
                "mean": ("mse", "mae", "normalized_bias", "zero_actual_excess_units"),
                "interval": ("coverage", "mean_score", "mean_width"),
            }.items():
                for name in names:
                    value = segment[side][component][name]
                    if value is not None:
                        result[f"{prefix}.{side}.{component}.{name}"] = float(value)
    return result
