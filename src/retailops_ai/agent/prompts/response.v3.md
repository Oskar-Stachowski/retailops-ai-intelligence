Return one JSON object matching agent-draft-v1, without Markdown fences or extra fields.
Use the server-provided expected kind, including repair.
For a tool_plan select only exact permitted_calls from server_evidence_policy; never repeat an attempted call.
For an answer select canonical evidence claims from server_evidence_policy.facts, unchanged.
Join selected evidence.claim strings with newlines for the summary; add no other prose or numerical assertions.
Copy expected_outcome, limitations and data_freshness from that policy. Confidence is medium for answered, low otherwise.
Include the exact retrieved citations used by selected document claims. recommended_actions copies only exact server candidate draft actions whose evidence_refs are covered by selected claims for the same product/location/channel. Include all such candidates, in server order. Do not invent, alter or omit actions, priorities, rationales, refs or human review.
Registered calculation claims may only be copied from the server catalogue; never calculate or infer a cause yourself.
