# AI12 — complete native read adapter catalog

The server can assemble all eight adapters with
`retailops_ai.adapters.native_assistant_tools.native_assistant_tools`. It accepts
already bound readers and pinned knowledge retrieval; tool arguments never carry
database addresses, filesystem paths, provider endpoints or identities. Source
verification occurs when the inventory/sales readers are initialized, not once
again for each tool call. Environments remain `local` and `test`.

| Tool | Native dependency | Evidence and limits |
| --- | --- | --- |
| Sales | `QualifiedSalesReader` | Verified Source/Curated/full Raw DQ/day coverage; complete causal days |
| Inventory | `NativeInventoryReader` | Verified physical snapshots and effective fulfillment routes |
| Forecast | `PostgresV12ForecastReader` | Canonical production v12 namespace, complete native forecast grid |
| Stockout | `PostgresStockoutReader` and the same inventory reader | Verified complete native outputs, physical grants, causal routes, exact 7-day horizon |
| Anomalies | `PostgresNativeAnomalyReader` | Complete immutable batch and enrolled release verified before scope projection |
| Operations | `PostgresNativeOperationsReader` | Scoped Producer `realtime_event_log`, native v1 sales/return processing over 15 minutes |
| Model status | `PostgresV12Catalog` and `EnvironmentAnomalyCatalog` | Scoped v12/anomaly publication and approval metadata; deployment remains `not_attested` |
| Knowledge | `PinnedKnowledgeTool` | Existing verified AI11 index pin and authorized retrieval |

## Stockout

The stockout adapter requires both selling access and explicit physical grants,
plus `stockout:read` and `inventory:read`. It resolves routes at the requested
origin, narrows the same principal to the resolved physical locations and calls
the existing native reader once. Unknown current inventory does not prevent
resolving a known route; the stockout output's own verified inventory evidence
and freshness determine whether it can support a risk fact. Its Source/Curated
lineage must equal that of the verified mapping.

The result preserves the native status, nullable probability, risk band, policy
and calibrator IDs, version/release/run IDs, PIT factors and native read
freshness. `already_stockout` is not probability 1, and `insufficient_data` is not
probability 0. No numeric decision threshold is invented from a policy ID.
Physical risk shared by two selling scopes is not additive. A partial page,
missing physical series, stale read or missing route withholds the full scope.
The requested horizon must be exactly the native seven days after the origin.

## Anomalies

The adapter requires both `anomalies:read` and the native `anomaly:read`; it never
adds a grant to the actor. Its reader selects the latest causal complete batch
visible to the requested scope. Inside a read-only repeatable-read transaction,
it verifies the privately loaded complete batch, row hash, decision hash, census,
batch identity and enrolled/completed release and version. Only then does it
project authorized `sale_completed` decisions for the exact requested days.
The database statement timeout is three seconds; the private census is bounded
to 10,000 rows and 16 MiB before loading the complete records.

Every requested product/location/day must be present and scored/current. Unknown
days or a partial window cannot become a clean negative. Native scoring origin,
batch cutoff, generation time, detector version, release, threshold and score
definition remain available. An unequal observation/expectation is not an alert;
only the saved detector's `alert=true` can create an anomaly review candidate.
The native seven-day freshness policy is checked again at decision time.

## Operations

The existing Producer dashboard endpoint exposes global counters and consumer
state without product/store/channel filters. This adapter instead uses a
server-owned read connection and parameterized filters on the native v1 payload
identifiers. Its repeatable-read transaction is read only, has a three-second
statement timeout and reads at most 10,001 records to enforce a 10,000-event
budget. No error messages, raw payloads or global consumer counters enter tool
results. Event types without all three scope identifiers are excluded.

The evidence contains pending, processed, failed/dead-lettered and duplicate
counts, the actual last ingest/processing timestamps and measured maximum
ingest-to-processing latency. A row updated after the requested cutoff cannot
be rewound from this mutable projection: it marks the whole requested scope
not evaluable. An empty source is not evidence that the consumer is healthy.
The actual read timestamp is retained; a historical request older than the
300-second observation bound is rejected.

**Consumer heartbeat and Kafka lag are not observed by this source.** The
adapter always preserves `stream_status=not_observed` and
`consumer_lag_seconds=null`. Processing latency is a distinct measurement and
does not become Kafka lag. End-to-end stream health still needs an authorized,
scoped observation source; no service is started or reconfigured here.

## Model status

Model reads require `model:read`, `forecast:read` and native `anomaly:read`. Both
catalogs receive the exact narrowed selling scope. Their native view hashes
must bind that scope, projection and principal. Pagination, environment and
canonical model namespaces are checked; mechanics/development namespaces are
excluded. Anomaly catalog reads also use a bounded read-only transaction.

The tool reports visible versions and an optional recorded approved release,
with the actual metadata observation time. Its `as_of` is the checked catalog
observation, not a substituted historical request timestamp. A publication
visible in a scope does not prove coverage of every requested series. Aliases
and deployed versions remain null; deployment is `not_attested`, drift is
`not_run`, and runtime freshness is unknown. The current response contract uses
`predictions.status=missing` for that unobserved runtime, without a fabricated
timestamp. This catalog covers v12 and anomaly models; stockout version/release
pins are preserved by the stockout tool, not presented as a live catalog scan.

## Runtime and acceptance

Native proofs are lossless optional fields in the existing eight result
envelopes. Historical fixture results omit those fields. The executor validates
the serialized proof again, exact requests, environment, independent grants and
decision-time freshness. Safe traces retain source and model release references.
HTTP admission records the extra native capabilities, so removing an underlying
read grant also removes access to a persisted trace. Native stockout remains
excluded from the legacy replenishment suggestion join; a recorded approval
does not supply its required deployment attestation.

The new active candidate family is `.native-tools.v1`. Published inventory,
sales and forecast candidate families, model artifacts, canonical golden labels
and earlier evidence remain unchanged. Question routes remain proposed, pending
review. [Acceptance receipt](evidence/12-native-tools.md) distinguishes adapter
mechanics and offline fake-chat checks from actual PostgreSQL/Producer/LLM
runtime acceptance. **AI12 remains `in_progress`.** Production bindings, scoped
heartbeat/Kafka observations, native suggestion policy/AI10 delivery, accepted
question labels and budgeted real Sonnet/Titan acceptance remain open.
