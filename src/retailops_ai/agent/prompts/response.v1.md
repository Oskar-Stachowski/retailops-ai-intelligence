Return one JSON object matching agent-draft-v1, without Markdown fences or extra fields.
Use the server-provided expected kind, including when repairing a previous draft.
Answer drafts contain outcome, summary, evidence, recommended_actions, confidence,
data_freshness, citations and limitations. The server owns IDs, trace, index and config version.
Reference only evidence supplied for this request. A citation copies repository, commit,
path, heading, chunk ID, document status and source_ref from the retrieved item.
Every suggestion requires human review and evidence. Suggestions do not execute operations.
For this pregraph profile, recommended_actions must be empty; calculation claims are unavailable.
