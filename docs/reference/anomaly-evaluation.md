# Anomaly evaluation protocol 1.0.0

The offline evaluator is separate from feature building and inference. It joins
explicit business truth to saved detector decisions. Raw DQ injections are a
separate pipeline-action reconciliation, never model labels.

Every requested series has one prediction for every business date. Missing
predictions fail evaluation. An independent complete truth window declares
whether absence of a business episode means a clean observation. Missing or
immature truth and insufficient inputs are excluded from observation metrics
and counted separately. Null metrics remain `not_evaluable`; no positives do not
produce perfect recall, and no alerts do not produce perfect precision.

Observation metrics include precision, recall, false alerts per 1,000 scored
mature clean observations, high-severity precision and stepwise average precision
with tied scores evaluated together. Event/currency segments and business types
are reported separately. Both detector families use the same requested census.

An episode is matched only to an alert of the same event/product/location/channel/
currency, with zero early lead and at most one business day after its end. Truth
episodes may not overlap. Where late tolerance overlaps a following episode, an
alert belongs to at most one episode, selected by earliest end then episode ID.
Repeated alerts count once for episode recall and have a separate repeat count.
Unscored episodes remain in the episode recall denominator. Episodes crossing
evaluation boundaries or lacking mature complete truth are explicitly censored.

Detection delay measures actual scoring availability: elapsed days from episode
start and seconds from availability of its first evidence. A closed-day detector
does not imply realtime detection. Average delay reports coverage over detected
episodes; undetected episodes remain visible in recall and the episode table.

The policy and evaluation identity bind metric definitions, truth, saved
predictions, window and as-of cutoff. Thresholds and training are never modified
by evaluation. Quality gates, dataset sizes and configuration selection must be
frozen before opening the final portfolio test. AI07 remains open while those
gates and lifecycle/serving acceptance are incomplete.
