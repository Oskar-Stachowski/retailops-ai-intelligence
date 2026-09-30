UV ?= $(if $(wildcard .tools/bin/uv),.tools/bin/uv,uv)
GITLEAKS ?= gitleaks
ENV_FILE ?= .env.example
UV_RUN = $(UV) run --locked --extra snapshot --extra forecast

.PHONY: bootstrap lint type-check test docs-check handoff-check snapshot-import-check curated-check forecast-calendar-check forecast-features-check forecast-manifests-check forecast-baselines-check forecast-models-check forecast-backtest-check forecast-quality-check forecast-run-check package check secrets ci-local serve contracts contracts-check compose-up compose-down compose-config compose-smoke mlflow-store-smoke

bootstrap:
	$(UV) sync --locked --extra snapshot --extra forecast

lint:
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .

type-check:
	$(UV_RUN) mypy

test:
	$(UV_RUN) pytest

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

forecast-run-check:
	$(UV_RUN) python scripts/check_forecast_run.py

package:
	$(UV) build --no-build-isolation

check: lint type-check test docs-check forecast-runtime-check handoff-check snapshot-import-check curated-check forecast-calendar-check forecast-features-check forecast-manifests-check forecast-baselines-check forecast-models-check forecast-backtest-check forecast-quality-check forecast-run-check contracts-check package compose-config

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

contracts-check:
	$(UV_RUN) python scripts/update_intelligence_contracts.py --check
	$(UV_RUN) python scripts/update_access_contracts.py --check
	$(UV_RUN) python scripts/update_knowledge_contracts.py --check
	$(UV_RUN) python scripts/update_forecast_contracts.py --check
	$(UV_RUN) python scripts/update_model_lifecycle_contracts.py --check
	$(UV_RUN) python scripts/update_forecast_job_contracts.py --check

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
