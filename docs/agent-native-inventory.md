# AI12 — verified physical inventory snapshots

`NativeInventoryReader` binds server-owned Source import and Curated paths in
`local`/`test`. Startup verifies both parents, rebuilds Curated from the original
import and requires the entire manifest to match. A plausible quantity with
recomputed Curated hashes is insufficient. Loading also rechecks the selected
tables' typed digests before retaining frozen, private records. Limits are
100,000 parent rows and 128 MiB; ordinary queries use indexed memory and perform
no export, model training, database operation or producer import.

The request keeps the selling grain. The reader resolves an effective half-open
native fulfillment route at the exact UTC cutoff, selecting the unique latest
available version. It preserves the distinct physical stock location, route ID,
version, availability and source-row hash. Identity requires the operator role,
`assistant:query`, `inventory:read`, the complete selling scope and an explicit
physical grant covering every product and resolved stock location. The existing
access-policy format associates that physical grant with `stockout:read`.
The adapter narrows the same principal before reading; no identity is supplied
in tool arguments. The executor checks physical scope again independently.

`NativeInventoryEvidence` retains the exact request, Source/Curated IDs and
descriptor hashes, qualification runtime hash and one point for every requested
product × selling-location combination. Snapshot quantities remain integer
`pcs`, checked for reconciliation and exact conversion to the tool's existing
`unit` representation. Native snapshot ID, business date, interval, partial-day
flag, measurement timestamp and causal availability remain in the evidence.
No conversion to cases, currencies or unconstrained demand is performed.

The latest snapshot at the cutoff is checked even when its quantity is unknown;
an older known snapshot does not replace it. Future availability, missing routes,
missing snapshots, ambiguous mappings and stale observations fail closed. A
snapshot must be at most 300 seconds old at the request cutoff, and the executor
applies its possibly stricter freshness limit again at decision time. The result
uses the oldest actual measurement timestamp across a complete scope, rather
than relabelling old stock with the request timestamp.

Any unavailable point withholds the entire scope. No partial total or zero is
inferred; explicit zero is accepted only from a known snapshot. Shared physical
stock is reported separately for each mapped selling grain with an explicit
non-additivity statement. It is not allocated or summed across those grains.
The planner checks the full scope against the evaluated graph's existing row
budget before admission. Native inventory is excluded from the legacy
replenishment suggestion join until native risk/model policy is qualified.

The optional `native_view` field is omitted for historical fixture results,
preserving canonical golden normalization. New `.native-inventory.v1` candidate
manifests bind this implementation and schemas. Published `.native-sources.v1`
and earlier candidates, labels, receipts and model artifacts remain unchanged.

Tests include the original tiny inventory Source fixture, an independently
summed native ledger, parent rebinding, a resealed false quantity, causal cutoffs,
physical authorization, unknown/stale points, conflicting routes and the actual
HTTP → reviewed planner → reader → graph → stored response/trace path. The HTTP
test freezes time for the historical fixture and explicitly uses fake chat and
`CaptureStore`; it does not establish PostgreSQL, production or LLM quality
acceptance. The accepted tool snapshot retains full native evidence in memory;
the Assistant persists the resulting claims and safe trace references.

AI12 remains **in_progress**. Remaining work covers qualified anomaly, stockout,
operations and model-status adapters; production bindings; native suggestion
policy and AI10 outbox delivery; accepted question labels; and real Sonnet/Titan
acceptance within the remaining budget.
