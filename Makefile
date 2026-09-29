UV ?= $(if $(wildcard .tools/bin/uv),.tools/bin/uv,uv)
GITLEAKS ?= gitleaks
ENV_FILE ?= .env.example
UV_RUN = $(UV) run --locked --extra snapshot

.PHONY: bootstrap lint type-check test docs-check handoff-check snapshot-import-check package check secrets ci-local serve contracts contracts-check compose-up compose-down compose-config compose-smoke

bootstrap:
	$(UV) sync --locked --extra snapshot

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

package:
	$(UV) build --no-build-isolation

check: lint type-check test docs-check handoff-check snapshot-import-check contracts-check package compose-config

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

contracts-check:
	$(UV_RUN) python scripts/update_intelligence_contracts.py --check
	$(UV_RUN) python scripts/update_access_contracts.py --check
	$(UV_RUN) python scripts/update_knowledge_contracts.py --check

compose-up:
	$(UV_RUN) python scripts/local_stack.py up

compose-down:
	$(UV_RUN) python scripts/local_stack.py down

compose-config:
	$(UV_RUN) python scripts/local_stack.py config

compose-smoke:
	$(UV_RUN) python scripts/verify_local_stack.py
