UV ?= $(if $(wildcard .tools/bin/uv),.tools/bin/uv,uv)
GITLEAKS ?= gitleaks
ENV_FILE ?= .env.example
UV_RUN = $(UV) run --locked --extra snapshot --extra forecast

.PHONY: bootstrap lint type-check test docs-check handoff-check snapshot-import-check curated-check forecast-calendar-check forecast-features-check forecast-manifests-check forecast-baselines-check forecast-models-check forecast-backtest-check forecast-quality-check forecast-run-check package check secrets ci-local serve contracts contracts-check compose-up compose-down compose-config compose-smoke mlflow-store-smoke forecast-acceptance-check forecast-remediation-check

bootstrap:
	$(UV) sync --locked --extra snapshot --extra forecast

lint:
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .

type-check:
	$(UV_RUN) mypy
	$(UV_RUN) python scripts/check_native_snapshot_types.py

test:
	$(UV_RUN) python -m pytest --durations=30

docs-check:
	$(UV_RUN) python scripts/check_repository.py

handoff-check:
	$(UV_RUN) python scripts/check_snapshot_handoff.py

snapshot-import-check:
	$(UV_RUN) python scripts/check_snapshot_import.py

curated-check:
	$(UV_RUN) python scripts/check_curated.py

forecast-calendar-check:
	$(UV_RUN) python scripts/check_forecast_calendar.py

forecast-features-check:
	$(UV_RUN) python scripts/check_forecast_features.py

forecast-manifests-check:
	$(UV_RUN) python scripts/check_forecast_manifests.py

forecast-baselines-check:
	$(UV_RUN) python scripts/check_forecast_baselines.py

forecast-models-check:
	$(UV_RUN) python scripts/check_forecast_models.py

forecast-backtest-check:
	$(UV_RUN) python scripts/check_forecast_backtest.py

forecast-quality-check:
	$(UV_RUN) python scripts/check_forecast_quality.py

forecast-remediation-check:
	$(UV_RUN) python scripts/check_forecast_remediation.py

forecast-run-check:
	$(UV_RUN) python scripts/check_forecast_run.py

forecast-acceptance-check:
	$(UV_RUN) python scripts/check_ai04_acceptance.py

package:
	$(UV) build --no-build-isolation

check: lint type-check test docs-check forecast-runtime-check handoff-check snapshot-import-check curated-check forecast-calendar-check forecast-features-check forecast-manifests-check forecast-baselines-check forecast-models-check forecast-backtest-check forecast-quality-check forecast-run-check contracts-check package compose-config forecast-remediation-check forecast-acceptance-check observation-replay-check

secrets:
	$(GITLEAKS) git . --redact --no-banner
	$(GITLEAKS) dir . --redact --no-banner

ci-local: check secrets

serve:
	$(UV_RUN) retailops-ai serve --env-file "$(ENV_FILE)"

contracts:
	$(UV_RUN) python scripts/update_http_contracts.py
	$(UV_RUN) python scripts/update_intelligence_contracts.py
	$(UV_RUN) python scripts/update_access_contracts.py
	$(UV_RUN) python scripts/update_knowledge_contracts.py
	$(UV_RUN) python scripts/update_forecast_contracts.py
	$(UV_RUN) python scripts/update_model_lifecycle_contracts.py
	$(UV_RUN) python scripts/update_forecast_job_contracts.py
	$(UV_RUN) python scripts/update_v12_runtime_contracts.py
	$(UV_RUN) python scripts/update_v12_inference_contracts.py
	$(UV_RUN) python scripts/update_v12_lifecycle_contracts.py
	$(UV_RUN) python scripts/update_v12_batch_contracts.py
	$(UV_RUN) python scripts/update_intelligence_event_contracts.py

contracts-check:
	$(UV_RUN) python scripts/update_observation_replay_contract.py --check
	$(UV_RUN) python scripts/check_source_bundle_contract.py
	$(UV_RUN) python scripts/update_source_rest_contract.py --check
	$(UV_RUN) python scripts/check_intelligence_delivery.py
	$(UV_RUN) python scripts/update_intelligence_contracts.py --check
	$(UV_RUN) python scripts/update_access_contracts.py --check
	$(UV_RUN) python scripts/update_knowledge_contracts.py --check
	$(UV_RUN) python scripts/update_forecast_contracts.py --check
	$(UV_RUN) python scripts/update_model_lifecycle_contracts.py --check
	$(UV_RUN) python scripts/update_forecast_job_contracts.py --check
	$(UV_RUN) python scripts/update_v12_runtime_contracts.py --check
	$(UV_RUN) python scripts/update_v12_inference_contracts.py --check
	$(UV_RUN) python scripts/update_v12_lifecycle_contracts.py --check
	$(UV_RUN) python scripts/update_v12_batch_contracts.py --check
	$(UV_RUN) python scripts/update_intelligence_event_contracts.py --check

compose-up:
	$(UV_RUN) python scripts/local_stack.py up

compose-down:
	$(UV_RUN) python scripts/local_stack.py down

compose-config:
	$(UV_RUN) python scripts/local_stack.py config

compose-smoke:
	$(UV_RUN) python scripts/verify_local_stack.py

mlflow-store-smoke:
	$(UV_RUN) python scripts/check_mlflow_store.py

.PHONY: v12-lifecycle-smoke
v12-lifecycle-smoke:
	$(UV_RUN) python scripts/check_v12_lifecycle.py

.PHONY: v12-queue-smoke
v12-queue-smoke:
	$(UV_RUN) python scripts/check_v12_queue.py

.PHONY: v12-publication-smoke
v12-publication-smoke:
	$(UV_RUN) python scripts/check_v12_publication.py

.PHONY: v12-metadata-smoke
v12-metadata-smoke:
	$(UV_RUN) python scripts/check_v12_metadata.py

.PHONY: v12-backup-smoke
v12-backup-smoke:
	$(UV_RUN) python scripts/check_v12_backup.py

.PHONY: model-lifecycle-smoke
model-lifecycle-smoke:
	$(UV_RUN) python scripts/check_model_lifecycle.py

.PHONY: lifecycle-store-smoke
lifecycle-store-smoke:
	$(UV_RUN) python scripts/check_lifecycle_store.py

.PHONY: forecast-queue-smoke
forecast-queue-smoke:
	$(UV_RUN) python scripts/check_forecast_queue.py

.PHONY: forecast-runtime-check
forecast-runtime-check:
	$(UV_RUN) python scripts/check_forecast_runtime.py

.PHONY: forecast-input-store-smoke
forecast-input-store-smoke:
	$(UV_RUN) python scripts/check_forecast_input_store.py

.PHONY: forecast-publication-smoke
forecast-publication-smoke:
	$(UV_RUN) python scripts/check_forecast_publication.py

.PHONY: forecast-read-smoke
forecast-read-smoke:
	$(UV_RUN) python scripts/check_forecast_read.py

.PHONY: model-catalog-smoke
model-catalog-smoke:
	$(UV_RUN) python scripts/check_model_catalog.py

.PHONY: evaluations-smoke
evaluations-smoke:
	$(UV_RUN) python scripts/check_evaluations.py

.PHONY: integration-replay-test integration-failure-test
integration-replay-test:
	$(UV_RUN) python scripts/check_intelligence_delivery.py
	$(UV_RUN) python scripts/update_intelligence_event_contracts.py --check
	$(UV_RUN) python -m pytest tests/test_intelligence_events.py tests/test_intelligence_delivery.py tests/test_observation_replay.py

integration-failure-test:
	REQUIRE_AI10_OUTBOX_TESTS=1 $(UV_RUN) python -m pytest tests/test_intelligence_outbox.py

.PHONY: observation-persistence-test
observation-persistence-test:
	REQUIRE_AI10_OBSERVATION_TESTS=1 AI10_OBSERVATION_REPORT=artifacts/ai10-durable-observation-replay.json $(UV_RUN) python -m pytest tests/test_observation_store_postgres.py --junitxml=artifacts/ai10-durable-observation-replay-tests.xml

.PHONY: observation-broker-test observation-consumer
observation-broker-test:
	REQUIRE_AI10_OBSERVATION_BROKER_TESTS=1 AI10_OBSERVATION_BROKER_REPORT=artifacts/ai10-observation-broker.json $(UV) run --locked --project tools/intelligence-delivery python -m pytest tests/test_observation_broker_runtime.py --junitxml=artifacts/ai10-observation-broker-tests.xml

observation-consumer:
	$(UV) run --locked --project tools/intelligence-delivery python scripts/observation_consumer.py $(ARGS)

.PHONY: source-rest-check source-rest-read
.PHONY: source-bundle-check
source-bundle-check:
	$(UV_RUN) python scripts/check_source_bundle_contract.py

.PHONY: observation-replay-check
observation-replay-check:
	$(UV_RUN) python scripts/check_observation_replay.py --report artifacts/ai10-observation-replay.json

source-rest-check:
	$(UV_RUN) python scripts/update_source_rest_contract.py --check
	$(UV_RUN) python -m pytest tests/test_source_rest.py

source-rest-read:
	$(UV_RUN) python -m retailops_ai.source_rest.cli $(ARGS)

.PHONY: intelligence-delivery-bootstrap intelligence-outbox
intelligence-delivery-bootstrap:
	$(UV) sync --locked --project tools/intelligence-delivery

intelligence-outbox:
	$(UV) run --locked --project tools/intelligence-delivery python scripts/intelligence_outbox.py $(ARGS)
