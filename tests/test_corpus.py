import hashlib
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from retailops_ai.adapters.git_documents import CorpusError, GitDocuments
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.contracts import REPOSITORIES, CorpusManifest, CorpusRegistry
from retailops_ai.pipelines.corpus import build_candidate, load_registry

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")
assert GIT is not None


def git(path, *args):
    return (
        subprocess.check_output([GIT, "-C", str(path), *args], stderr=subprocess.DEVNULL)
        .decode()
        .strip()
    )


def commit(path):
    git(path, "add", ".")
    git(path, "commit", "-qm", "Fixture snapshot")
    return git(path, "rev-parse", "HEAD")


def validate(payload):
    return CorpusRegistry.model_validate_json(json.dumps(payload))


@pytest.fixture
def corpus(tmp_path):
    repositories = {}
    sources = []
    for i, name in enumerate(REPOSITORIES):
        repo = tmp_path / str(i)
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.name", "Fixture author")
        git(repo, "config", "user.email", "fixture@example.invalid")
        git(repo, "remote", "add", "origin", f"https://github.com/{name}.git")
        (repo / "docs/evidence").mkdir(parents=True)
        (repo / "docs/plans").mkdir()
        (repo / "src").mkdir()
        (repo / "src/main.py").write_text("print('fixture')\n")
        text = f"# Registered {i}\n\nUntrusted reference text.\n".encode()
        (repo / "docs/registered.md").write_bytes(text)
        (repo / "docs/new.md").write_text("# Unreviewed\n")
        (repo / "docs/.private.md").write_text("private-body-marker\n")
        (repo / ".env").write_text("PRIVATE=private-body-marker\n")
        (repo / "raw").mkdir()
        (repo / "raw/facts.md").write_text("private-body-marker\n")
        (repo / "docs/plans/later.md").write_text("# Planned\n")
        sha = commit(repo)
        repositories[name] = repo
        sources.append(
            {
                "repository": name,
                "commit_sha": sha,
                "allowed_roots": ["docs"],
                "documents": [
                    {
                        "path": "docs/registered.md",
                        "title": f"Registered {i}",
                        "byte_sha256": hashlib.sha256(text).hexdigest(),
                        "document_type": "guide",
                        "document_status": "specified",
                        "access_class": "public_project",
                        "fact_scope": "Fixture documentation, without runtime claims.",
                        "implementation_refs": [],
                        "verification": None,
                    }
                ],
            }
        )
    return {
        "schema_version": "1.0",
        "policy_version": "registered-markdown-v1",
        "environment": "test",
        "review_state": "proposed",
        "review_owner": "fixture-maintainer",
        "sources": sources,
    }, repositories


def verified(corpus):
    payload, repos = corpus
    payload = deepcopy(payload)
    source = payload["sources"][0]
    repo = repos[source["repository"]]
    code_commit = source["commit_sha"]
    evidence = {
        "result": "success",
        "commit": code_commit,
        "verified_at": "2026-09-28T08:07:26+00:00",
    }
    raw = json.dumps(evidence).encode()
    (repo / "docs/evidence/proof.json").write_bytes(raw)
    source["commit_sha"] = commit(repo)
    doc = source["documents"][0]
    doc["document_status"] = "verified"
    doc["verification"] = {
        "repository": source["repository"],
        "commit_sha": source["commit_sha"],
        "path": "docs/evidence/proof.json",
        "byte_sha256": hashlib.sha256(raw).hexdigest(),
        "verified_at": evidence["verified_at"],
        "verified_commit": code_commit,
        "commit_pointer": "/commit",
        "verified_at_pointer": "/verified_at",
        "scope": doc["fact_scope"],
        "result_pointer": "/result",
        "expected_result": "success",
    }
    return payload, repos


def test_same_git_objects_are_deterministic_across_clone_and_dirty_worktree(corpus, tmp_path):
    payload, repos = corpus
    before = build_candidate(validate(payload), repos)
    copies = {}
    for i, (name, repo) in enumerate(repos.items()):
        clone = tmp_path / f"copy-{i}"
        subprocess.run([GIT, "clone", "-q", "--no-hardlinks", str(repo), str(clone)], check=True)
        git(clone, "remote", "set-url", "origin", f"https://github.com/{name}.git")
        (repo / "docs/registered.md").write_text("Uncommitted replacement with private-body-marker")
        (repo / "docs/untracked.md").write_text("private-body-marker")
        copies[name] = clone
    assert build_candidate(validate(payload), repos) == before
    assert build_candidate(validate(payload), copies) == before
    reordered = deepcopy(payload)
    reordered["sources"].reverse()
    assert build_candidate(validate(reordered), repos) == before
    assert "private-body-marker" not in before.model_dump_json()
    assert before.lifecycle == "candidate"
    assert before.content_trust == "untrusted_reference"


def test_new_revision_preserves_content_identity_and_updates_citation(corpus):
    payload, repos = corpus
    before = build_candidate(validate(payload), repos)
    source = payload["sources"][0]
    repo = repos[source["repository"]]
    (repo / "unrelated.txt").write_text("new commit\n")
    source["commit_sha"] = commit(repo)
    after = build_candidate(validate(payload), repos)
    old, new = before.documents[-1], after.documents[-1]
    # Sources sort AI before platform; platform is the original first source.
    assert old.repository == source["repository"]
    assert new.document_id == old.document_id and new.content_id == old.content_id
    assert new.source_ref != old.source_ref
    assert after.corpus_id != before.corpus_id


def test_content_change_and_removal_invalidate_only_that_document(corpus):
    payload, repos = corpus
    before = build_candidate(validate(payload), repos)
    source = payload["sources"][0]
    repo = repos[source["repository"]]
    raw = b"# Changed\n\nNew content.\n"
    (repo / "docs/registered.md").write_bytes(raw)
    source["commit_sha"] = commit(repo)
    source["documents"][0]["byte_sha256"] = hashlib.sha256(raw).hexdigest()
    after = build_candidate(validate(payload), repos)
    assert before.documents[0] == after.documents[0]
    assert before.documents[-1].content_id != after.documents[-1].content_id
    (repo / "docs/registered.md").unlink()
    source["commit_sha"] = commit(repo)
    with pytest.raises(CorpusError, match="registered_document_missing"):
        build_candidate(validate(payload), repos)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "docs/../.env",
        "/forbidden/private.md",
        "https://example.invalid/a.md",
        "docs/raw/facts.md",
        "docs/simulation_truth/labels.md",
        "docs/uploads/file.md",
        "docs/private/file.md",
        "docs/.env.md",
        "docs/secret.key.md",
        "docs/a.log.md",
        "docs/registered.md/",
        "docs//registered.md",
        "docs/%2e%2e/private.md",
        "docs/a\\private.md",
        "docs/a\nprivate.md",
        "other/registered.md",
        "docs/a.json",
    ],
)
def test_private_arbitrary_and_non_markdown_paths_are_rejected(corpus, path):
    payload, _ = corpus
    payload["sources"][0]["documents"][0]["path"] = path
    with pytest.raises(ValidationError):
        validate(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "owner",
        "source",
        "missing_repo",
        "duplicate_repo",
        "zero_commit",
        "short_commit",
        "extra_field",
        "missing_status",
        "missing_access",
        "outside_root",
        "duplicate_document",
        "approved",
        "implemented_without_code",
        "verified_without_evidence",
    ],
)
def test_registry_rejects_ambiguous_missing_metadata_and_false_approval(corpus, mutation):
    payload, _ = corpus
    source = payload["sources"][0]
    document = source["documents"][0]
    if mutation == "version":
        payload["schema_version"] = "2.0"
    elif mutation == "owner":
        payload["review_owner"] = ""
    elif mutation == "source":
        source["repository"] = "unknown/repo"
    elif mutation == "missing_repo":
        payload["sources"].pop()
    elif mutation == "duplicate_repo":
        payload["sources"][1] = source
    elif mutation == "zero_commit":
        source["commit_sha"] = "0" * 40
    elif mutation == "short_commit":
        source["commit_sha"] = source["commit_sha"][:7]
    elif mutation == "extra_field":
        document["unknown"] = "private-body-marker"
    elif mutation == "missing_status":
        document.pop("document_status")
    elif mutation == "missing_access":
        document.pop("access_class")
    elif mutation == "outside_root":
        source["allowed_roots"] = ["docs/other"]
    elif mutation == "duplicate_document":
        source["documents"].append(document)
    elif mutation == "approved":
        payload["review_state"] = "approved"
    elif mutation == "implemented_without_code":
        document["document_status"] = "implemented"
    else:
        document["document_status"] = "verified"
    with pytest.raises(ValidationError):
        validate(payload)


def test_plans_cannot_be_promoted_by_attaching_successful_evidence(corpus):
    payload, _ = verified(corpus)
    document = payload["sources"][0]["documents"][0]
    document["path"] = "docs/plans/later.md"
    with pytest.raises(ValidationError, match="plans_are_specified"):
        validate(payload)


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("checksum", "evidence_checksum_mismatch"),
        ("result", "evidence_result_mismatch"),
        ("commit", "verified_commit_mismatch"),
        ("time", "verification_time_mismatch"),
        ("pointer", "evidence_binding_missing"),
        ("missing", "registered_document_missing"),
    ],
)
def test_verified_source_requires_exact_existing_proof(corpus, mutation, code):
    payload, repos = verified(corpus)
    assert build_candidate(validate(payload), repos).documents[-1].document_status == "verified"
    proof = payload["sources"][0]["documents"][0]["verification"]
    if mutation == "checksum":
        proof["byte_sha256"] = "f" * 64
    elif mutation == "result":
        proof["expected_result"] = "passed"
    elif mutation == "commit":
        proof["verified_commit"] = "f" * 40
    elif mutation == "time":
        proof["verified_at"] = "2026-09-27T00:00:00Z"
    elif mutation == "pointer":
        proof["result_pointer"] = "/missing"
    else:
        proof["path"] = "docs/evidence/absent.json"
    with pytest.raises(CorpusError, match=code):
        build_candidate(validate(payload), repos)


@pytest.mark.parametrize("mutation", ["none", "checksum", "missing", "stale", "zero", "private"])
def test_implemented_requires_code_in_the_registered_source_revision(corpus, mutation):
    payload, repos = corpus
    source = payload["sources"][0]
    repo = repos[source["repository"]]
    document = source["documents"][0]
    document["document_status"] = "implemented"
    reference = {
        "repository": source["repository"],
        "commit_sha": source["commit_sha"],
        "path": "src/main.py",
        "byte_sha256": hashlib.sha256((repo / "src/main.py").read_bytes()).hexdigest(),
    }
    document["implementation_refs"] = [reference]
    if mutation == "checksum":
        reference["byte_sha256"] = "f" * 64
    elif mutation == "missing":
        reference["path"] = "src/absent.py"
    elif mutation == "stale":
        (repo / "src/main.py").unlink()
        source["commit_sha"] = commit(repo)
    elif mutation == "zero":
        reference["commit_sha"] = "0" * 40
    elif mutation == "private":
        reference["path"] = "src/PRIVATE/credentials.py"
    if mutation == "none":
        assert (
            build_candidate(validate(payload), repos).documents[-1].document_status == "implemented"
        )
    elif mutation in {"stale", "zero", "private"}:
        with pytest.raises(ValidationError):
            validate(payload)
    else:
        with pytest.raises(CorpusError):
            build_candidate(validate(payload), repos)


def test_verified_cannot_reuse_evidence_removed_from_the_registered_revision(corpus):
    payload, repos = verified(corpus)
    source = payload["sources"][0]
    repo = repos[source["repository"]]
    (repo / "docs/evidence/proof.json").unlink()
    source["commit_sha"] = commit(repo)
    with pytest.raises(ValidationError, match="reference_source_revision_mismatch"):
        validate(payload)


def test_exclusions_are_reported_without_reading_forbidden_documents(corpus, monkeypatch):
    payload, repos = corpus
    original = GitDocuments.read
    reads = []

    def record(self, revision, path):
        reads.append(path)
        return original(self, revision, path)

    monkeypatch.setattr(GitDocuments, "read", record)
    manifest = build_candidate(validate(payload), repos)
    assert reads == ["docs/registered.md", "docs/registered.md"]
    assert len(manifest.excluded_documents) == 6
    assert {x.reason for x in manifest.excluded_documents} == {
        "not_registered",
        "credentials_or_private_path",
    }
    assert "private-body-marker" not in manifest.model_dump_json()


@pytest.mark.parametrize("kind", ["symlink", "executable", "invalid_utf8", "nul", "empty", "large"])
def test_unsafe_git_content_is_rejected(corpus, kind):
    payload, repos = corpus
    source = payload["sources"][0]
    repo = repos[source["repository"]]
    path = repo / "docs/registered.md"
    if kind == "symlink":
        path.unlink()
        path.symlink_to("../.env")
    elif kind == "executable":
        path.chmod(0o755)
    else:
        path.write_bytes(
            {"invalid_utf8": b"\xff", "nul": b"# bad\0", "empty": b"", "large": b"a" * 500_001}[
                kind
            ]
        )
    source["commit_sha"] = commit(repo)
    if kind != "symlink":
        source["documents"][0]["byte_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(CorpusError):
        build_candidate(validate(payload), repos)


def test_origin_and_missing_revision_are_rejected(corpus):
    payload, repos = corpus
    source = payload["sources"][0]
    repo = repos[source["repository"]]
    source["commit_sha"] = "f" * 40
    with pytest.raises(CorpusError, match="git_object_unavailable"):
        build_candidate(validate(payload), repos)
    git(repo, "remote", "set-url", "origin", "https://example.invalid/private")
    with pytest.raises(CorpusError, match="repository_binding_mismatch"):
        build_candidate(validate(payload), repos)


def test_git_replace_cannot_change_pinned_content(corpus):
    payload, repos = corpus
    before = build_candidate(validate(payload), repos)
    source = payload["sources"][0]
    repo = repos[source["repository"]]
    (repo / "docs/registered.md").write_text("# Secret replacement\n")
    replacement = commit(repo)
    git(repo, "replace", source["commit_sha"], replacement)
    assert build_candidate(validate(payload), repos) == before


def test_environment_and_access_metadata_change_corpus_identity(corpus):
    payload, repos = corpus
    before = build_candidate(validate(payload), repos)
    payload["environment"] = "production"
    after = build_candidate(validate(payload), repos)
    assert after.corpus_id != before.corpus_id and after.documents == before.documents
    payload["sources"][0]["documents"][0]["access_class"] = "restricted"
    assert build_candidate(validate(payload), repos).corpus_id != after.corpus_id


def test_lf_identity_and_exact_duplicates_are_reported(corpus):
    payload, repos = corpus
    for source in payload["sources"]:
        raw = b"# Duplicate\r\n" if source is payload["sources"][0] else b"# Duplicate\n"
        repo = repos[source["repository"]]
        (repo / "docs/registered.md").write_bytes(raw)
        source["commit_sha"] = commit(repo)
        source["documents"][0]["byte_sha256"] = hashlib.sha256(raw).hexdigest()
    result = build_candidate(validate(payload), repos)
    assert result.documents[0].content_id == result.documents[1].content_id
    assert result.documents[0].byte_sha256 != result.documents[1].byte_sha256
    assert len(result.duplicate_content_groups) == 1


def test_manifest_identity_coverage_and_binding_cannot_be_forged(corpus):
    payload, repos = corpus
    manifest = build_candidate(validate(payload), repos)
    for mutation in [
        "id",
        "coverage",
        "source_ref",
        "metadata",
        "config",
        "duplicates",
        "excluded",
    ]:
        value = json.loads(manifest.model_dump_json())
        if mutation == "id":
            value["corpus_id"] = "corpus-sha256-" + "f" * 64
        elif mutation == "coverage":
            value["documents"].pop()
        elif mutation == "source_ref":
            value["documents"][0]["source_ref"] = "git:wrong"
        elif mutation == "metadata":
            value["documents"][0]["access_class"] = "restricted"
        elif mutation == "config":
            value["corpus_config_id"] = "corpus-config-sha256-" + "f" * 64
        elif mutation == "duplicates":
            value["duplicate_content_groups"] = [[value["documents"][0]["document_id"]]]
        else:
            value["excluded_documents"][0]["path"] = value["documents"][0]["path"]
        if mutation != "id":
            value["corpus_id"] = "corpus-sha256-" + canonical_sha256(
                {k: v for k, v in value.items() if k != "corpus_id"}
            )
        with pytest.raises(ValidationError):
            CorpusManifest.model_validate_json(json.dumps(value))


def test_immutable_output_and_cli_error_redaction(corpus, tmp_path):
    payload, repos = corpus
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(payload))
    output = tmp_path / "candidate.json"
    command = [
        sys.executable,
        "-m",
        "retailops_ai",
        "corpus-check",
        "--registry",
        str(registry),
        "--retailops-repo",
        str(repos[REPOSITORIES[0]]),
        "--ai-repo",
        str(repos[REPOSITORIES[1]]),
        "--output",
        str(output),
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["documents"] == 2 and summary["lifecycle"] == "candidate"
    assert output.stat().st_mode & 0o777 == 0o600
    before = output.read_bytes()
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 2 and output.read_bytes() == before
    payload["sources"][0]["documents"][0]["path"] = "docs/private-body-marker.md"
    registry.write_text(json.dumps(payload))
    result = subprocess.run(command[:-2], text=True, capture_output=True)
    assert result.returncode == 2
    assert "private-body-marker" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr and str(tmp_path) not in result.stderr
    assert list(tmp_path.glob("tmp*")) == []


@pytest.mark.parametrize(
    "raw",
    [b'{"schema_version":"1.0","schema_version":"2.0"}', b'{"x":NaN}', b"[" * 2000, b" " * 500_001],
)
def test_registry_json_is_bounded_and_unambiguous(tmp_path, raw):
    path = tmp_path / "registry.json"
    path.write_bytes(raw)
    with pytest.raises((ValueError, RecursionError)):
        load_registry(path)


def test_schema_snapshots_and_candidate_contract(corpus):
    payload, repos = corpus
    for name, model, value in [
        ("corpus-registry", CorpusRegistry, payload),
        (
            "corpus-manifest",
            CorpusManifest,
            json.loads(build_candidate(validate(payload), repos).model_dump_json()),
        ),
    ]:
        schema = json.loads((ROOT / f"contracts/knowledge/v1/{name}.v1.schema.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(value, schema)
        assert schema["properties"] == model.model_json_schema()["properties"]
