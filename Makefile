UV ?= $(if $(wildcard .tools/bin/uv),.tools/bin/uv,uv)
GITLEAKS ?= gitleaks
ENV_FILE ?= .env.example

.PHONY: bootstrap lint type-check test docs-check package check secrets ci-local serve contracts

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

check: lint type-check test docs-check package

secrets:
	$(GITLEAKS) git . --redact --no-banner
	$(GITLEAKS) dir . --redact --no-banner

ci-local: check secrets

serve:
	$(UV) run --locked retailops-ai serve --env-file "$(ENV_FILE)"

contracts:
	$(UV) run --locked python scripts/update_http_contracts.py
