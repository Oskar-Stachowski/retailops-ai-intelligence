# AI04 v13: fixed validation mean correction

AI04 remains **not_ready**. Complete v12 qualification evaluated all 64 fixed
cohorts and passed 221 of 224 gates. Three real mean-MSE regressions remain
failed; independent replay and durable export of that result continue on the
unchanged v12 implementation. The old sources, snapshots and campaign results
are retained.

The next recipe uses the same separate median/MAE and mean/MSE-plus-bias goals.
The quality policy and its thresholds remain unchanged. Zero sales have absolute
error and false-positive-unit diagnostics without a fabricated percentage.
Interval width divided by very small actual sales remains diagnostic; interval
score and coverage determine qualification.

For ordinary volumes the candidate mean becomes
`max(0, reference_mean + fixed_weight * freshly_fitted_pooled_offset)`.
Weights come exclusively from the retained v12 validation observations, from
the declared grid `0, 0.25, 0.5, 0.75, 1`. The prefix labels precede the last
seven validation-origin days by the existing 15-day purge. Selection requires
MSE nonregression and the existing 10% bias bound on both later selection and
full validation; ties choose the smallest adjustment.

The 72 component decisions cover three folds, three ordinary volume bins and
eight categories. Two components had no admissible inner correction and retain
their reference means. Their failed constraints remain recorded. The combined
variant passed all 112 standard validation gates. The existing reference was
selected using outer validation, so this is an internal calibration diagnostic,
not independent qualification or an inner causal forecast backtest.

The zero-history estimator, reference median and interval retain their v12
behavior. The v12 recipe still serializes identically and retains its prediction
bytes; explicit recipe version 3 requires declared weights and their validation
receipt hashes. Missing ordinary weight groups retain the reference mean.

The method is recorded in
[protocol version 3](../contracts/forecast/v3/mean-correction.protocol.json) and
[the fixed weights](../contracts/forecast/v3/mean-weights.from-v12-validation.json).
The protocol remains a draft until exact-code CI and the final campaign freeze
are complete. A reproducible validation-only selector is available as
`scripts/check_forecast_mean_validation_weights.py --campaign PATH --freeze PATH
--output NEW_PATH`; it preserves prior reports and checks role before accessing
an observation payload.

The next qualification uses all 64 separate synthetic seeds 730001–730064,
reserved in the shared exposure registry before generation. All 224 gates and
the split, maturity and purge rules remain fixed. v12 holdouts cannot qualify
the revised model. No new source has been generated. Before activation, measure
free space against the full resource plan; the preliminary initial budget is
49.484375 GiB, including retained archives, bounded workspace and safety margin.

`ready` requires every quality gate, exact independent replay, durable export,
verification using the frozen wheel outside the checkout, and accepted code.
Passing validation alone cannot satisfy it. Serving, registry promotion and
production deployment belong to later stages.
