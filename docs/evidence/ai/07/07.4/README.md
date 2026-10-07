# AI07.4 — independent snapshot 1.2 import

Status: **local transport/import acceptance passed**. This milestone does not
complete AI07 or qualify a detector. Source 2.8 carries two independent business
scenario fixtures; public/private snapshot 1.2 variants preserve explicit truth
isolation. See [the reference](../../../../reference/anomaly-snapshot-12.md) and
[verification.json](verification.json).

Consumer runtime: `17f4709a02bab1c6efc00f41d3512315e97c57fd`.
Producer runtime: `b59aca8e2fe70a76efde2192b63b5bf839104bac`.
The wheel SHA256 is
`95edfd1f89ae1d50c899335a3a61f3a3914c777aef77c67f384c87084bd07fa0`.
The [frozen fixture](../../../../../data/fixtures/anomaly-v1_2.md) records its own
checksum and source lineage.

Two separate processes load the installed wheel from a detached virtualenv.
Pinned dependency packages are reused; the AI source checkout is not on the
module search path and the producer `data` module is unavailable. Each process
imports/reimports/verifies demand-public, demand-private, physical-public and
physical-private snapshots. IDs match across processes; all input and published
file hashes remain unchanged. Public imports have 43 tables; explicit private
imports have 55 and separate plan/configuration/qualification artifacts. Each
acceptance case takes 9.68–16.67 seconds, below 300 seconds / 1024 MiB limits.

94 existing import/handoff tests and 8 new anomaly import tests passed. The new
tests exercise immutable publication, truth opt-in, extra-file rejection,
checksum-resealed private plan binding changes, unknown versions and explicit
rejection by unsupported curated builders. Ruff/format and full Mypy (193
modules) pass. The wheel build and offline no-dependency installation pass.

The next AI07 slice must implement curated anomaly/DQ semantics and PIT-safe
features before fitting/evaluating baseline and Isolation Forest and integrating
with AI05 lifecycle. Import alone changes no model readiness or existing forecast
approval. No AI05 session state, DB, broker or service was modified. Exact-head
remote CI is recorded on the PR.
