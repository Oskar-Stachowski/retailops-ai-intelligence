import hashlib
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from retailops_ai.adapters.git_documents import CorpusError
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.chunks import ChunkerConfig, ChunkManifest
from retailops_ai.knowledge.contracts import REPOSITORIES, CorpusRegistry
from retailops_ai.pipelines.chunks import build_chunks, load_chunker_config

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")
assert GIT is not None


def git(root, *args):
    return (
        subprocess.check_output([GIT, "-C", str(root), *args], stderr=subprocess.DEVNULL)
        .decode()
        .strip()
    )


def commit(root):
    git(root, "add", ".")
    git(root, "commit", "-qm", "Fixture snapshot")
    return git(root, "rev-parse", "HEAD")


def registry(value):
    return CorpusRegistry.model_validate_json(json.dumps(value))


@pytest.fixture
def config():
    return load_chunker_config(ROOT / "knowledge/chunker.v1.json")


@pytest.fixture
def sources(tmp_path):
    repos = {}
    registered = []
    for i, name in enumerate(REPOSITORIES):
        repo = tmp_path / f"repo-{i}"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.name", "Fixture author")
        git(repo, "config", "user.email", "fixture@example.invalid")
        git(repo, "remote", "add", "origin", f"https://github.com/{name}.git")
        (repo / "docs").mkdir()
        raw = f"# Document {i}\n\n## Scope\n\nBody {i}.\n".encode()
        (repo / "docs/guide.md").write_bytes(raw)
        (repo / "docs/unreviewed.md").write_text("private-body-marker")
        (repo / ".env").write_text("PRIVATE=private-body-marker")
        registered.append(
            {
                "repository": name,
                "commit_sha": commit(repo),
                "allowed_roots": ["docs"],
                "documents": [
                    {
                        "path": "docs/guide.md",
                        "title": f"Document {i}",
                        "byte_sha256": hashlib.sha256(raw).hexdigest(),
                        "document_type": "guide",
                        "document_status": "specified",
                        "access_class": "public_project",
                        "fact_scope": "Synthetic parser fixture only.",
                        "implementation_refs": [],
                        "verification": None,
                    }
                ],
            }
        )
        repos[name] = repo
    return {
        "schema_version": "1.0",
        "policy_version": "registered-markdown-v1",
        "environment": "test",
        "review_state": "proposed",
        "review_owner": "fixture-maintainer",
        "sources": registered,
    }, repos


def replace(sources, text, index=0):
    payload, repos = sources
    source = payload["sources"][index]
    raw = text.encode()
    (repos[source["repository"]] / "docs/guide.md").write_bytes(raw)
    source["commit_sha"] = commit(repos[source["repository"]])
    source["documents"][0]["byte_sha256"] = hashlib.sha256(raw).hexdigest()


def build(sources, config):
    payload, repos = sources
    return build_chunks(registry(payload), config, repos)


def platform_chunks(manifest):
    return [c for c in manifest.chunks if c.repository == REPOSITORIES[0]]


def assert_source_coverage(manifest, sources):
    _, repos = sources
    for document, mapping in zip(manifest.corpus.documents, manifest.documents, strict=True):
        raw = subprocess.check_output(
            [
                GIT,
                "-C",
                str(repos[document.repository]),
                "show",
                f"{document.commit_sha}:{document.path}",
            ]
        )
        text = raw.decode().replace("\r\n", "\n").replace("\r", "\n")
        spans = [o.location for o in mapping.omitted_blocks]
        for chunk in manifest.chunks:
            if chunk.document_id != document.document_id:
                continue
            for span in chunk.occurrences:
                assert text[span.start_offset : span.end_offset] == chunk.text
                assert span.start_line == text[: span.start_offset].count("\n") + 1
                assert span.end_line == text[: span.end_offset - 1].count("\n") + 1
                assert (
                    span.source_ref == f"{document.source_ref}#L{span.start_line}-L{span.end_line}"
                )
            spans.extend(chunk.occurrences)
        cursor = 0
        for span in sorted(spans, key=lambda span: span.start_offset):
            assert not text[cursor : span.start_offset].strip()
            cursor = span.end_offset
        assert not text[cursor:].strip()


def test_heading_path_title_setext_skips_and_exact_source_citations(sources, config):
    replace(
        sources,
        """Preamble before the title.

# **Project** `API` [guide](https://example.invalid)

## Overview

Normal paragraph.

#### Deep

Deep paragraph.

Replacement
-----------

Setext paragraph.

> # Quoted heading
> quoted content

~~~python
# Not a section
print('literal')
~~~

    # Indented code
    print('literal')
""",
    )
    result = build(sources, config)
    chunks = platform_chunks(result)
    assert chunks[0].heading_path == ()
    assert [h.title for h in chunks[1].heading_path] == ["Project API guide", "Overview"]
    assert [(h.level, h.title) for h in chunks[2].heading_path] == [
        (1, "Project API guide"),
        (2, "Overview"),
        (4, "Deep"),
    ]
    assert [h.title for h in chunks[3].heading_path] == ["Project API guide", "Replacement"]
    assert [c.block_type for c in chunks] == [
        "paragraph",
        "paragraph",
        "paragraph",
        "paragraph",
        "blockquote",
        "fence",
        "code_block",
    ]
    assert all(
        h.title not in {"Quoted heading", "Not a section", "Indented code"}
        for c in chunks
        for h in c.heading_path
    )
    assert_source_coverage(result, sources)


def test_navigation_comments_and_exact_duplicates_keep_accountable_ranges(sources, config):
    replace(
        sources,
        """# Guide

## Spis treści

- [API](#api)
- [Limits](#limits)

### Nested navigation

navigation text

## API

<!-- ignored metadata -->

Repeated paragraph.

Repeated paragraph.

[up](#guide) | [limits](#limits)

See [limits](#limits) before configuring.

[external guide](https://example.invalid)

## API

Repeated paragraph.
""",
    )
    result = build(sources, config)
    chunks = platform_chunks(result)
    assert [c.text for c in chunks] == [
        "Repeated paragraph.",
        "See [limits](#limits) before configuring.",
        "[external guide](https://example.invalid)",
    ]
    assert len(chunks[0].occurrences) == 3
    reasons = {o.reason for d in result.documents for o in d.omitted_blocks}
    assert {"navigation_section", "anchor_navigation", "html_comment"} <= reasons
    assert "private-body-marker" not in result.model_dump_json()
    assert_source_coverage(result, sources)


def test_tables_lists_html_and_reference_definitions_preserve_substantive_source(sources, config):
    replace(
        sources,
        """# Guide

[ref]: https://example.invalid/api "API"

Use [API][ref].

| Field | Meaning |
|---|---|
| status | specified |

1. First item
   - nested item
2. Second item

<div>
literal HTML, no renderer
</div>

---
""",
    )
    result = build(sources, config)
    assert [c.block_type for c in platform_chunks(result)] == [
        "reference_definition",
        "paragraph",
        "table",
        "ordered_list",
        "html_block",
    ]
    assert_source_coverage(result, sources)


@pytest.mark.parametrize(
    "text",
    [
        "# Heading only\n",
        "# Guide\n\n<!-- comment -->\n",
        "# Spis treści\n\n[up](#top)\n",
        "---\n",
    ],
)
def test_document_without_substantive_blocks_is_explicit(sources, config, text):
    replace(sources, text)
    result = build(sources, config)
    mapping = result.documents[-1]
    assert mapping.chunk_ids == () and mapping.outcome == "no_retrievable_content"
    assert platform_chunks(result) == []
    assert_source_coverage(result, sources)


@pytest.mark.parametrize("kind", ["paragraph", "fence", "table", "list", "unicode", "long_line"])
def test_large_blocks_are_bounded_utf8_without_losing_source(sources, config, kind):
    texts = {
        "paragraph": "Sentence about a limit. " * 100,
        "fence": "```text\n" + "# literal code\n" * 150 + "```\n",
        "table": "| Field | Value |\n|---|---|\n" + "| status | specified |\n" * 150,
        "list": "- item content and limits\n" * 150,
        "unicode": "żółć 🛒 漢字 é " * 300,
        "long_line": "🛒" * 1000,
    }
    replace(sources, "# Guide\n\n" + texts[kind])
    small = config.model_copy(update={"max_utf8_bytes": 256})
    result = build(sources, small)
    chunks = platform_chunks(result)
    assert sum(len(c.occurrences) for c in chunks) > 1
    assert all(0 < len(c.text.encode()) <= 256 for c in chunks)
    assert all(c.token_estimate == (len(c.text.encode()) + 3) // 4 for c in chunks)
    assert_source_coverage(result, sources)


def test_new_revision_new_lines_and_ordinal_preserve_unchanged_chunk_ids(sources, config):
    before = build(sources, config)
    old = platform_chunks(before)[0]
    replace(sources, "# Document 0\n\nIntro added.\n\n## Scope\n\nBody 0.\n")
    after = build(sources, config)
    unchanged = next(c for c in platform_chunks(after) if c.text == old.text)
    assert unchanged.chunk_id == old.chunk_id and unchanged.content_checksum == old.content_checksum
    assert unchanged.chunk_index != old.chunk_index
    assert unchanged.occurrences != old.occurrences and unchanged.source_ref != old.source_ref
    assert before.chunk_manifest_id != after.chunk_manifest_id
    assert before.chunks[0] == after.chunks[0]  # other repo unaffected
    assert_source_coverage(after, sources)


def test_edited_and_removed_blocks_are_absent_from_new_candidate(sources, config):
    replace(sources, "# Guide\n\n## A\n\nKept text.\n\n## B\n\nOld text.\n\nRemove text.\n")
    before = build(sources, config)
    old = {c.text: c.chunk_id for c in platform_chunks(before)}
    replace(sources, "# Guide\n\n## A\n\nKept text.\n\n## B\n\nNew text.\n")
    after = build(sources, config)
    new = {c.text: c.chunk_id for c in platform_chunks(after)}
    assert new["Kept text."] == old["Kept text."]
    assert old["Old text."] not in new.values() and old["Remove text."] not in new.values()
    assert before.chunks[0] == after.chunks[0]


def test_removed_registered_document_requires_registry_update_and_leaves_no_orphans(
    sources, config
):
    payload, repos = sources
    source = payload["sources"][0]
    repo = repos[source["repository"]]
    extra = deepcopy(source["documents"][0])
    extra["path"] = "docs/other.md"
    raw = b"# Other\n\nRetained document.\n"
    (repo / extra["path"]).write_bytes(raw)
    extra["byte_sha256"] = hashlib.sha256(raw).hexdigest()
    source["documents"].append(extra)
    source["commit_sha"] = commit(repo)
    before = build(sources, config)
    old_ids = {
        c.chunk_id
        for c in before.chunks
        if c.path == "docs/guide.md" and c.repository == source["repository"]
    }
    (repo / "docs/guide.md").unlink()
    source["commit_sha"] = commit(repo)
    with pytest.raises(CorpusError, match="registered_document_missing"):
        build(sources, config)
    source["documents"].pop(0)
    after = build(sources, config)
    assert not old_ids & {c.chunk_id for c in after.chunks}
    assert len(after.documents) == 2
    assert_source_coverage(after, sources)


def test_content_context_access_environment_and_configuration_identity(sources, config):
    payload, _ = sources
    before = build(sources, config)
    original = platform_chunks(before)[0]
    payload["environment"] = "production"
    payload["sources"][0]["documents"][0]["access_class"] = "restricted"
    changed_access = build(sources, config)
    assert platform_chunks(changed_access)[0].chunk_id == original.chunk_id
    assert platform_chunks(changed_access)[0].access_class == "restricted"
    assert changed_access.chunk_manifest_id != before.chunk_manifest_id
    changed_config = build(sources, config.model_copy(update={"max_utf8_bytes": 256}))
    assert platform_chunks(changed_config)[0].chunk_id != original.chunk_id
    replace(sources, "# Document 0\n\n## Renamed scope\n\nBody 0.\n")
    changed_heading = platform_chunks(build(sources, config))[0]
    assert changed_heading.chunk_id != original.chunk_id
    assert changed_heading.content_checksum == original.content_checksum


def test_identical_body_in_different_heading_or_document_retains_source_identity(sources, config):
    replace(sources, "# Guide\n\n## A\n\nSame text.\n\n## B\n\nSame text.\n")
    replace(sources, "# Guide\n\n## A\n\nSame text.\n", index=1)
    result = build(sources, config)
    assert len(result.chunks) == 3
    assert len({c.chunk_id for c in result.chunks}) == 3
    assert len({c.content_checksum for c in result.chunks}) == 1


def test_clones_registry_order_worktree_and_line_endings_do_not_change_content(
    sources, config, tmp_path
):
    payload, repos = sources
    before = build(sources, config)
    copies = {}
    for i, (name, repo) in enumerate(repos.items()):
        target = tmp_path / f"copy-{i}"
        subprocess.run([GIT, "clone", "-q", "--no-hardlinks", str(repo), str(target)], check=True)
        git(target, "remote", "set-url", "origin", f"https://github.com/{name}.git")
        (repo / "docs/guide.md").write_text("private-body-marker")
        copies[name] = target
    reordered = deepcopy(payload)
    reordered["sources"].reverse()
    assert build(sources, config) == before
    assert build_chunks(registry(reordered), config, copies) == before
    replace(sources, "# Document 0\r\n\r\n## Scope\r\n\r\nBody 0.\r\n")
    after = build(sources, config)
    assert platform_chunks(after)[0].chunk_id == platform_chunks(before)[0].chunk_id


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_utf8_bytes", 255),
        ("max_utf8_bytes", 16001),
        ("max_utf8_bytes", "2048"),
        ("chunker_version", "unknown"),
        ("parser_version", "4.0.0"),
        ("parser", "arbitrary"),
        ("parser_preset", "gfm-like"),
        ("navigation_policy", "any"),
        ("size_estimator", "model-tokens"),
        ("duplicate_policy", "all-documents"),
        ("extra_field", True),
    ],
)
def test_chunker_config_rejects_ambiguous_unpinned_algorithms(config, field, value):
    payload = config.model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        ChunkerConfig.model_validate_json(json.dumps(payload))


def rehash_manifest(value):
    value["chunk_manifest_id"] = "chunks-sha256-" + canonical_sha256(
        {key: item for key, item in value.items() if key != "chunk_manifest_id"}
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "checksum",
        "token_estimate",
        "citation",
        "range",
        "overlap",
        "ordinal",
        "missing_chunk",
        "missing_document",
        "orphan",
        "reordered",
        "metadata",
        "corpus",
        "config",
        "id",
        "outcome",
    ],
)
def test_manifest_rejects_tampering_incomplete_graphs_and_stale_bindings(sources, config, mutation):
    value = json.loads(build(sources, config).model_dump_json())
    chunk = value["chunks"][0]
    if mutation == "checksum":
        chunk["text"] = "Altered content"
    elif mutation == "token_estimate":
        chunk["token_estimate"] += 1
    elif mutation == "citation":
        chunk["occurrences"][0]["source_ref"] = "git:wrong#L1-L2"
    elif mutation == "range":
        chunk["occurrences"][0]["end_offset"] += 1
    elif mutation == "overlap":
        value["documents"][0]["omitted_blocks"].append(value["documents"][0]["omitted_blocks"][0])
    elif mutation == "ordinal":
        chunk["chunk_index"] = 1
    elif mutation == "missing_chunk":
        value["chunks"].pop()
    elif mutation == "missing_document":
        value["documents"].pop()
    elif mutation == "orphan":
        value["documents"][0]["chunk_ids"] = []
        value["documents"][0]["outcome"] = "no_retrievable_content"
    elif mutation == "reordered":
        value["chunks"].reverse()
    elif mutation == "metadata":
        chunk["access_class"] = "restricted"
    elif mutation == "corpus":
        value["corpus_id"] = "corpus-sha256-" + "f" * 64
    elif mutation == "config":
        value["chunker"]["max_utf8_bytes"] = 256
    elif mutation == "id":
        chunk["chunk_id"] = "chunk-sha256-" + "f" * 64
    else:
        value["documents"][0]["outcome"] = "no_retrievable_content"
    rehash_manifest(value)
    with pytest.raises(ValidationError):
        ChunkManifest.model_validate_json(json.dumps(value))


def test_cli_atomic_write_safe_failure_no_config_dependency(sources, config, tmp_path):
    payload, repos = sources
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload))
    output = tmp_path / "chunks.json"
    args = [
        sys.executable,
        "-m",
        "retailops_ai",
        "chunk-build",
        "--registry",
        str(path),
        "--chunker-config",
        str(ROOT / "knowledge/chunker.v1.json"),
        "--retailops-repo",
        str(repos[REPOSITORIES[0]]),
        "--ai-repo",
        str(repos[REPOSITORIES[1]]),
        "--output",
        str(output),
    ]
    environment = {**os.environ, "APP_ENV": "private-body-marker"}
    result = subprocess.run(args, text=True, capture_output=True, env=environment)
    assert result.returncode == 0
    assert json.loads(result.stdout)["chunks"] == 2
    assert "Body 0" not in result.stdout and "private-body-marker" not in output.read_text()
    assert output.stat().st_mode & 0o777 == 0o600
    before = output.read_bytes()
    result = subprocess.run(args, text=True, capture_output=True, env=environment)
    assert result.returncode == 2 and output.read_bytes() == before
    source = payload["sources"][0]
    (repos[source["repository"]] / "docs/guide.md").unlink()
    source["commit_sha"] = commit(repos[source["repository"]])
    path.write_text(json.dumps(payload))
    missing_output = tmp_path / "missing.json"
    result = subprocess.run([*args[:-1], str(missing_output)], text=True, capture_output=True)
    assert result.returncode == 2 and not missing_output.exists()
    assert "Traceback" not in result.stderr and str(tmp_path) not in result.stderr


def test_parser_version_drift_and_chunk_limit_block_build(sources, config, monkeypatch):
    monkeypatch.setattr("retailops_ai.pipelines.chunks.version", lambda _: "0.0.0")
    with pytest.raises(CorpusError, match="markdown_parser_version_mismatch"):
        build(sources, config)
    monkeypatch.setattr("retailops_ai.pipelines.chunks.version", lambda _: config.parser_version)
    monkeypatch.setattr("retailops_ai.pipelines.chunks.MAX_DOCUMENT_CHUNKS", 1)
    replace(sources, "# Guide\n\nParagraph A.\n\nParagraph B.\n")
    with pytest.raises(CorpusError, match="document_chunk_limit_exceeded"):
        build(sources, config)


def test_schema_snapshots_match_and_validate_candidates(sources, config):
    manifest = build(sources, config)
    for name, model, value in [
        ("chunker-config", ChunkerConfig, config),
        ("chunk-manifest", ChunkManifest, manifest),
    ]:
        schema = json.loads((ROOT / f"contracts/knowledge/v1/{name}.v1.schema.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(json.loads(value.model_dump_json()), schema)
        assert schema["properties"] == model.model_json_schema()["properties"]
