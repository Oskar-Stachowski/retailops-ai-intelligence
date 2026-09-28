UV ?= $(if $(wildcard .tools/bin/uv),.tools/bin/uv,uv)
GITLEAKS ?= gitleaks
ENV_FILE ?= .env.example

.PHONY: bootstrap lint type-check test docs-check package check secrets ci-local serve contracts contracts-check compose-up compose-down compose-config compose-smoke

bootstrap:
	$(UV) sync --locked

lint:
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .

type-check:
	$(UV) run --locked mypy

test:
	$(UV) run --locked pytest

docs-check:
	$(UV) run --locked python scripts/check_repository.py

package:
	$(UV) build --no-build-isolation

check: lint type-check test docs-check contracts-check package compose-config

secrets:
	$(GITLEAKS) git . --redact --no-banner
	$(GITLEAKS) dir . --redact --no-banner

ci-local: check secrets

serve:
	$(UV) run --locked retailops-ai serve --env-file "$(ENV_FILE)"

contracts:
	$(UV) run --locked python scripts/update_http_contracts.py
	$(UV) run --locked python scripts/update_intelligence_contracts.py
	$(UV) run --locked python scripts/update_access_contracts.py

contracts-check:
	$(UV) run --locked python scripts/update_intelligence_contracts.py --check
	$(UV) run --locked python scripts/update_access_contracts.py --check

compose-up:
	$(UV) run --locked python scripts/local_stack.py up

compose-down:
	$(UV) run --locked python scripts/local_stack.py down

compose-config:
	$(UV) run --locked python scripts/local_stack.py config

compose-smoke:
	$(UV) run --locked python scripts/verify_local_stack.py
