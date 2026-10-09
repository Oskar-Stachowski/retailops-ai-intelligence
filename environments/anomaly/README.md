# Frozen anomaly qualification dependencies

`qualification.uv.lock` is the immutable fit environment recorded by the AI07
primary and reference model capsules. It is copied from consumer commit
`fcd09f68187b8d21e9dff756a24688b790221023`; SHA-256:
`33c53d1a1f08d5c90b3b61c79e6aeb732f0eebc6e8be36e735c93ca277492587`.
It is an evidence reference, not a separate executable uv project.

AI12 adds LangGraph to the application. The OCI acceptance still loads both
unchanged saved models, verifies signatures and lineage, replays their saved
predictions and the complete six frozen evaluation inputs, and checks quality.
An explicit training lock allows this replay only when every original external
package record, including dependencies, markers and distribution hashes, is
unchanged. The application root may add only `langgraph==1.2.12`; its other
metadata and dependencies must remain equal. Python and resolver settings must
also remain equal. Without this explicit reference, a changed lock is rejected.

The new compatibility receipt binds the training and current runtime lock
separately. Model artifacts, fit times and training qualification lock remain
unchanged. Replay attests compatibility of these frozen models and inputs; it
does not attest deployment or drift monitoring. Required CI then performs the
full fresh OCI, PostgreSQL and MLflow acceptance with the current runtime lock.
