"""Read bounded, pinned Git objects; never open document paths in the worktree."""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from retailops_ai.knowledge.contracts import MAX_MARKDOWN_BYTES, Repository


class CorpusError(ValueError):
    """Stable error codes only; input values and Git stderr remain private."""


@dataclass(frozen=True)
class GitEntry:
    mode: str
    kind: str
    object_id: str


class GitDocuments:
    def __init__(self, root: Path, repository: Repository) -> None:
        self.root = root
        self.repository = repository
        self.git = shutil.which("git")
        if self.git is None:
            raise CorpusError("git_unavailable")
        # Replacements would make one recorded SHA refer to different content.
        self._run("rev-parse", "--git-dir")
        origin = self._run("config", "--get", "remote.origin.url").decode().strip()
        allowed = {
            f"https://github.com/{repository}.git",
            f"https://github.com/{repository}",
            f"git@github.com:{repository}.git",
        }
        if origin not in allowed:
            raise CorpusError("repository_binding_mismatch")
        self.trees: dict[str, dict[str, GitEntry]] = {}

    def _run(self, *arguments: str) -> bytes:
        try:
            result = subprocess.run(  # noqa: S603 - fixed Git commands and validated object IDs
                [str(self.git), "--no-replace-objects", "-C", str(self.root), *arguments],
                capture_output=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CorpusError("git_object_unavailable") from exc
        if result.returncode:
            raise CorpusError("git_object_unavailable")
        if len(result.stdout) > 4_000_000:
            raise CorpusError("git_response_too_large")
        return result.stdout

    def tree(self, revision: str) -> dict[str, GitEntry]:
        if revision not in self.trees:
            resolved = self._run("rev-parse", "--verify", revision + "^{commit}").decode().strip()
            if resolved != revision:
                raise CorpusError("commit_binding_mismatch")
            entries: dict[str, GitEntry] = {}
            try:
                for raw in self._run("ls-tree", "-rz", "--full-tree", revision).split(b"\0"):
                    if not raw:
                        continue
                    metadata, path = raw.split(b"\t", 1)
                    mode, kind, object_id = metadata.decode("ascii").split(" ")
                    entries[path.decode("utf-8")] = GitEntry(mode, kind, object_id)
            except (UnicodeError, ValueError) as exc:
                raise CorpusError("invalid_git_tree") from exc
            self.trees[revision] = entries
        return self.trees[revision]

    def read(self, revision: str, path: str) -> bytes:
        entry = self.tree(revision).get(path)
        if entry is None:
            raise CorpusError("registered_document_missing")
        if entry.mode != "100644" or entry.kind != "blob":
            raise CorpusError("non_regular_document")
        size = int(self._run("cat-file", "-s", entry.object_id))
        if not 0 < size <= MAX_MARKDOWN_BYTES:
            raise CorpusError("document_size_invalid")
        return self._run("cat-file", "blob", entry.object_id)
