# Business event-day qualification 1.0

AI07 can independently qualify the completeness of sales and scoped return event
days before producing an observed value for anomaly scoring. A sealed DQ replay's
finite parent coverage alone does not close a day. The separate producer artifact
provides explicit, available-at closure declarations from source 2.8.

```sh
retailops-ai-day-qualification build --replay-dir REPLAY --coverage-dir COVERAGE \
  --curated-dir CURATED --import-dir IMPORT --generated-root data/generated
retailops-ai-day-qualification verify --directory QUALIFICATION \
  --replay-dir REPLAY --coverage-dir COVERAGE --curated-dir CURATED --import-dir IMPORT
```

The consumer first independently reconstructs the full raw-DQ replay against the
verified source → snapshot → curated chain. It loads eight typed operational curated
tables under a 100,000-row / 128 MiB input budget and reconciles their digests. From
five of these tables it independently reconstructs every producer declaration,
checking source and table identities, exact canonical rows, policy, scope and seals.
No producer Python module, fault plan, anomaly truth or cohort flag is needed.

The grain is `(event_type, business_date, product_id, selling_location_id, channel,
currency)`, with UTC event dates. Default origins are 24 hours after the exclusive
sales day end and 72 hours after the return day end. `--as-of UTC_TIMESTAMP` builds
one historical delivery-time view. The policy and runtime are part of the artifact
ID. Querying a later cutoff does not change an earlier view.

| Status | Observed value | Scoring eligibility |
| --- | --- | --- |
| `qualified` | Native accepted units and amount, including an explicit known zero | true |
| `no_declaration` / `closure_unavailable` | unknown | false |
| `source_incomplete` / `location_closed` | unknown | false |
| `dq_missing_facts` / `dq_unattributed_quarantine` | unknown | false |

`score_eligible` reports only this completeness / DQ gate. Feature history,
context availability, censoring and accepted model lifecycle gates still apply.

Before a declaration's `known_at`, expected IDs or counts do not appear in the point.
After closure, every expected fact must have an accepted raw receipt available by
the cutoff. Returns also require accepted receipts for all relevant original
purchases. The value is computed from accepted facts, never from final aggregates
or the final daily observation. Native rejected claim quantities are reported
separately and do not count as refunded units. Missing grains remain unknown.

A quarantined record attributable to a native parent key leaves that required key
missing until a verified replacement is accepted. A replacement can qualify a later
view while the earlier one stays unknown. Unattributable quarantine withholds all
days at and after its receipt; it cannot be converted into a zero. Attribution uses
the operational business key; a conflicting canonical UUID cannot replace that key.
Declarations and output files must have a single hardlink, and a bounded read checks
file identity and content metadata on the same file descriptor. Progress records,
maximum event time and purchase-cohort maturity do not override these gates.

The return scope is strictly `purchases_in_parent_source_only`. The publisher
asserts closure for its verified complete finite synthetic export with bounded
native ingestion, through the applicable return-window tail. This does not establish
global returns, carry-in purchase coverage or a production source's day closure.

The exact output inventory is `qualified_days.jsonl`, `qualification_manifest.json`
and `manifest.sha256`. The content-addressed descriptor binds the full-DQ replay,
producer coverage, source parents, policy, code, packaged contracts, dependency
lock, Python version, output digest and status counts. Verification reconstructs
the parents and values rather than trusting a resealed artifact. Build retries and
publication races compare to material independently reconstructed in that invocation.

Output is bounded to 10,000 rows / 32 MiB. `make day-qualification-check` runs both
profiles twice in fresh isolated processes, with limits of 300 seconds / 1024 MiB
per process. It is included in `make check`, `make ci-local` and required CI. The
versioned public declarations are described in the [fixture](../../data/fixtures/day-coverage-v1.md).

The [qualified residual inputs](qualified-anomaly-inputs.md) query this gate
separately for fitting history and scoring outcomes. AI07 remains open:
baseline / Isolation Forest comparison, scenario evaluation and the final detector
lifecycle is still required. These artifacts explicitly leave model readiness
`not_qualified`; existing forecast and anomaly input contracts retain their meaning.
