UV ?= $(if $(wildcard .tools/bin/uv),.tools/bin/uv,uv)
GITLEAKS ?= gitleaks
ENV_FILE ?= .env.example
PROVIDER ?= fake
BEDROCK_ARGS ?=

.PHONY: bootstrap lint type-check test docs-check package check secrets ci-local serve contracts contracts-check compose-up compose-down compose-config compose-smoke agent-security-test agent-evaluate bedrock-smoke

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

check: lint type-check test docs-check contracts-check agent-evaluate package compose-config

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
	$(UV) run --locked python scripts/update_knowledge_contracts.py
	$(UV) run --locked python scripts/update_agent_contracts.py
	$(UV) run --locked python scripts/update_agent_chat_contracts.py
	$(UV) run --locked python scripts/update_agent_graph_contracts.py
	$(UV) run --locked python scripts/update_agent_bedrock_contracts.py
	$(UV) run --locked python scripts/update_assistant_contracts.py

contracts-check:
	$(UV) run --locked python scripts/update_intelligence_contracts.py --check
	$(UV) run --locked python scripts/update_access_contracts.py --check
	$(UV) run --locked python scripts/update_knowledge_contracts.py --check
	$(UV) run --locked python scripts/update_agent_contracts.py --check
	$(UV) run --locked python scripts/update_agent_chat_contracts.py --check
	$(UV) run --locked python scripts/update_agent_graph_contracts.py --check
	$(UV) run --locked python scripts/update_agent_bedrock_contracts.py --check
	$(UV) run --locked python scripts/update_assistant_contracts.py --check

agent-security-test:
	$(UV) run --locked pytest tests/test_agent_tools.py tests/test_agent_chat.py tests/test_agent_graph.py tests/test_agent_suggestions.py tests/test_agent_evaluation.py tests/test_assistant.py tests/test_bedrock_chat.py

agent-evaluate:
	$(UV) run --locked retailops-ai agent-evaluate --provider "$(PROVIDER)" --config agent/graph.evaluate.fake.v1.json --golden agent/golden.canonical.v1.json --release agent/evaluation-release.fake.v1.json --rag-golden knowledge/golden.semantic.v1.json --lock uv.lock

bedrock-smoke:
	$(UV) run --locked retailops-ai bedrock-smoke --config agent/graph.bedrock-smoke.v1.json --offline-config agent/graph.evaluate.fake.v1.json --golden agent/golden.canonical.v1.json --release agent/evaluation-release.fake.v1.json --rag-golden knowledge/golden.semantic.v1.json --lock uv.lock --profile agent/bedrock-smoke.v1.json $(BEDROCK_ARGS)

compose-up:
	$(UV) run --locked python scripts/local_stack.py up

compose-down:
	$(UV) run --locked python scripts/local_stack.py down

compose-config:
	$(UV) run --locked python scripts/local_stack.py config

compose-smoke:
	$(UV) run --locked python scripts/verify_local_stack.py
