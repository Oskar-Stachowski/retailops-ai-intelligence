import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from test_access import bearer, policy_file, problem
from test_chunks import config as config
from test_chunks import registry, replace
from test_chunks import sources as sources
from test_indexes import embedding_config as embedding_config

from retailops_ai.api.app import create_app
from retailops_ai.cli import main
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import KnowledgeAccess, Principal
from retailops_ai.knowledge.contracts import REPOSITORIES
from retailops_ai.knowledge.golden import GoldenSet
from retailops_ai.knowledge.retrieval import RetrievalRequest
from retailops_ai.pipelines.chunks import build_chunks
from retailops_ai.pipelines.golden import evaluate, load_golden_set, validate_labels
from retailops_ai.pipelines.indexes import build_index
from retailops_ai.pipelines.retrieval import (
    KnowledgeDenied,
    checked_vector,
    load_retrieval_config,
    search_candidate,
)
from retailops_ai.security.models import GrantTemplate

ROOT = Path(__file__).resolve().parents[1]


def retrieval_config():
    return load_retrieval_config(ROOT / "src/retailops_ai/knowledge/retrieval.default.json")


def reader(
    *,
    classes=("public_project",),
    statuses=("specified", "implemented", "verified"),
    repos=REPOSITORIES,
    environment="test",
    capability=True,
):
    return Principal(
        "fixture-reader",
        frozenset({"operator"}),
        frozenset({"knowledge:read"}) if capability else frozenset(),
        frozenset(),
        frozenset(),
        frozenset(),
        KnowledgeAccess(environment, frozenset(repos), frozenset(classes), frozenset(statuses)),
    )


def request(question="Body 0.", **changes):
    return RetrievalRequest.model_validate_json(
        json.dumps({"schema_version": "1.0", "question": question, **changes})
    )


def candidate(sources, config, embedding_config):
    value, repos = sources
    return build_index(build_chunks(registry(value), config, repos), embedding_config)


def test_exact_body_match_citations_budget_and_pin_identity(sources, config, embedding_config):
    index = candidate(sources, config, embedding_config)
    result = search_candidate(index, request(), reader(), retrieval_config())
    assert result.items[0].score == pytest.approx(1, abs=1e-7)
    assert result.items[0].chunk.text == "Body 0."
    assert result.index_id == index.manifest.index_id
    assert result.items[0].chunk.occurrences[0].source_ref.endswith("#L5-L5")
    assert result.content_trust == "untrusted_reference"
    assert result.answer_generation == "not_implemented"
    assert result.context_bytes <= 24000 and result.context_tokens <= 6000
    assert result == search_candidate(index, request(), reader(), retrieval_config())


@pytest.mark.parametrize(
    "field,value",
    [
        ("question", " "),
        ("question", "x" * 2001),
        ("question", "\0"),
        ("top_k", True),
        ("top_k", 6),
        ("max_context_tokens", 6001),
        ("role", "admin"),
        ("principal_id", "fake"),
        ("index_id", "arbitrary"),
        ("purpose", "works"),
        ("filters", {"repositories": []}),
        ("filters", {"document_statuses": ["specified", "specified"]}),
        ("filters", {"access_classes": ["all"]}),
        ("filters", {"path": "/etc/passwd"}),
    ],
)
def test_retrieval_request_is_bounded_and_cannot_supply_identity(field, value):
    with pytest.raises(ValidationError):
        request(**{field: value})


@pytest.mark.parametrize(
    "changes", [{"capability": False}, {"environment": "local"}, {"repos": (REPOSITORIES[1],)}]
)
def test_entire_requested_scope_denied_without_partial_expansion(
    sources, config, embedding_config, changes
):
    index = candidate(sources, config, embedding_config)
    with pytest.raises(KnowledgeDenied):
        search_candidate(
            index,
            request(filters={"repositories": list(REPOSITORIES)}),
            reader(**changes),
            retrieval_config(),
        )


def test_access_is_filtered_before_ranking_and_repeated_reads_are_isolated(
    sources, config, embedding_config
):
    sources[0]["sources"][0]["documents"][0]["access_class"] = "restricted"
    index = candidate(sources, config, embedding_config)
    privileged = search_candidate(
        index, request(), reader(classes=("public_project", "restricted")), retrieval_config()
    )
    assert privileged.items[0].chunk.access_class == "restricted"
    public = search_candidate(index, request(), reader(), retrieval_config())
    assert all(h.chunk.access_class == "public_project" for h in public.items)
    with pytest.raises(KnowledgeDenied):
        search_candidate(
            index, request(filters={"access_classes": ["restricted"]}), reader(), retrieval_config()
        )


def test_repository_type_status_and_history_filters(sources, config, embedding_config):
    sources[0]["sources"][0]["documents"][0]["document_status"] = "historical"
    index = candidate(sources, config, embedding_config)
    granted = reader(statuses=("specified", "historical"))
    default = search_candidate(index, request(), granted, retrieval_config())
    assert all(h.chunk.document_status != "historical" for h in default.items)
    history = search_candidate(index, request(purpose="history"), granted, retrieval_config())
    assert history.items and all(h.claim_kind == "historical_reference" for h in history.items)
    incompatible = search_candidate(
        index,
        request(purpose="history", filters={"document_statuses": ["specified"]}),
        granted,
        retrieval_config(),
    )
    assert incompatible.status == "insufficient_evidence"
    none = search_candidate(
        index, request(filters={"document_types": ["model_card"]}), granted, retrieval_config()
    )
    assert none.status == "insufficient_evidence" and none.context_bytes == 0
    one = search_candidate(
        index, request(filters={"repositories": [REPOSITORIES[1]]}), granted, retrieval_config()
    )
    assert all(h.chunk.repository == REPOSITORIES[1] for h in one.items)


def test_plan_cannot_be_reported_as_implementation_and_insufficient_context_is_empty(
    sources, config, embedding_config
):
    index = candidate(sources, config, embedding_config)
    result = search_candidate(
        index, request(purpose="implementation"), reader(), retrieval_config()
    )
    assert result.status == "insufficient_evidence"
    result = search_candidate(index, request(max_context_tokens=1), reader(), retrieval_config())
    assert result.status == "insufficient_evidence" and result.context_tokens == 0


def test_live_denial_applies_to_an_old_candidate_on_repeated_read(
    sources, config, embedding_config
):
    index = candidate(sources, config, embedding_config)
    first = search_candidate(index, request(), reader(), retrieval_config())
    denied = frozenset({first.items[0].chunk.document_id})
    second = search_candidate(index, request(), reader(), retrieval_config(), denied=denied)
    assert all(hit.chunk.document_id not in denied for hit in second.items)


def test_tie_break_and_source_diversity_survive_duplicate_text(sources, config, embedding_config):
    replace(sources, "# One\n\n## Scope\n\nSame body.\n\nOther text.\n", 0)
    replace(sources, "# Two\n\n## Scope\n\nSame body.\n", 1)
    index = candidate(sources, config, embedding_config)
    result = search_candidate(index, request("Same body.", top_k=2), reader(), retrieval_config())
    tied = sorted(c.chunk_id for c in index.chunks.chunks if c.text == "Same body.")
    assert [h.chunk.chunk_id for h in result.items] == tied
    assert len({h.chunk.repository for h in result.items}) == 2


@pytest.mark.parametrize(
    "vector", [(0.0,) * 32, (float("nan"),) * 32, (1.0,) * 8, (float("inf"),) * 32, (0.1,) * 32]
)
def test_invalid_query_embedding_rejected_before_sql(embedding_config, vector):
    with pytest.raises(ValueError):
        checked_vector(embedding_config, vector)


def test_injection_source_remains_reference_and_cannot_change_scope(
    sources, config, embedding_config
):
    attack = json.loads((ROOT / "tests/fixtures/rag/adversarial.v1.json").read_text())["documents"][
        0
    ]
    body = attack["body"]
    replace(sources, "# " + attack["heading"] + "\n\n" + body, 0)
    index = candidate(sources, config, embedding_config)
    result = search_candidate(index, request(body), reader(), retrieval_config())
    assert result.items[0].chunk.text == body
    assert result.content_trust == "untrusted_reference"
    assert result.answer_generation == "not_implemented"
    assert reader().roles == frozenset({"operator"})
    denied = search_candidate(
        index, request(body, purpose="verified_state"), reader(), retrieval_config()
    )
    assert denied.status == "insufficient_evidence"


def knowledge_policy(tmp_path):
    def add(value):
        value["grants"][0]["capabilities"].append("knowledge:read")
        value["grants"][0]["knowledge_scope"] = {
            "environment": "test",
            "repositories": list(REPOSITORIES),
            "access_classes": ["public_project"],
            "document_statuses": ["specified", "implemented", "verified"],
        }

    return policy_file(tmp_path, add)


def test_http_verified_credentials_scope_no_store_and_redacted_denials(
    tmp_path, sources, config, embedding_config
):
    index = candidate(sources, config, embedding_config)
    path, tokens = knowledge_policy(tmp_path)
    calls = []

    class Backend:
        def search(self, body, principal):
            calls.append(principal.principal_id)
            return search_candidate(index, body, principal, retrieval_config())

    settings = Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path)
    app = create_app(settings, knowledge_backend=Backend())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        body = request().model_dump(mode="json")
        problem(client.post("/api/v1/knowledge/search", json=body), 401)
        problem(
            client.post(
                "/api/v1/knowledge/search", json=body, headers=bearer(tokens["local-admin"])
            ),
            403,
        )
        expanded = deepcopy(body)
        expanded["filters"]["access_classes"] = ["restricted"]
        problem(
            client.post(
                "/api/v1/knowledge/search", json=expanded, headers=bearer(tokens["local-viewer"])
            ),
            403,
        )
        problem(
            client.post(
                "/api/v1/knowledge/search",
                json={**body, "role": "admin"},
                headers=bearer(tokens["local-viewer"]),
            ),
            422,
        )
        assert calls == []
        response = client.post(
            "/api/v1/knowledge/search",
            json=body,
            headers={**bearer(tokens["local-viewer"]), "X-Role": "admin"},
        )
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert calls == ["local-viewer"]
        assert response.json()["items"][0]["chunk"]["text"] == "Body 0."
        identity = client.get("/api/v1/identity", headers=bearer(tokens["local-viewer"])).json()
        assert identity["knowledge_scope"]["access_classes"] == ["public_project"]
    unavailable = create_app(settings)
    with TestClient(unavailable, base_url="http://127.0.0.1") as client:
        problem(
            client.post(
                "/api/v1/knowledge/search", json=body, headers=bearer(tokens["local-viewer"])
            ),
            503,
        )


def test_grant_requires_explicit_knowledge_scope_and_does_not_inherit_admin():
    v = json.loads((ROOT / "contracts/access/v1/grant-template.v1.example.json").read_text())
    v["grants"][1]["capabilities"].append("knowledge:read")
    with pytest.raises(ValidationError):
        GrantTemplate.model_validate_json(json.dumps(v))


def fixture_golden(index):
    v = json.loads((ROOT / "knowledge/golden.v1.json").read_text())
    first = index.chunks.chunks[0]
    v["index_id"] = index.manifest.index_id
    for case in v["cases"]:
        case["scope"] = {
            "environment": "test",
            "repositories": list(REPOSITORIES),
            "access_classes": ["public_project"],
            "document_statuses": ["specified"],
        }
        case["request"] = request(first.text).model_dump(mode="json")
        case["expected_sections"] = [
            {
                "repository": first.repository,
                "path": first.path,
                "heading_path": [h.title for h in first.heading_path],
                "document_status": first.document_status,
            }
        ]
        case["forbidden_sources"] = []
        case["acceptable_outcomes"] = ["ok"]
        case["answerability"] = "answerable"
        case["required_tools"] = ["search_knowledge"]
    v.pop("golden_set_id")
    v["golden_set_id"] = "golden-set-sha256-" + canonical_sha256(v)
    return GoldenSet.model_validate_json(json.dumps(v))


def test_perfect_fake_golden_report_still_cannot_activate(sources, config, embedding_config):
    index = candidate(sources, config, embedding_config)
    golden = fixture_golden(index)
    report = evaluate(index, golden, retrieval_config())
    assert report.recall_at_5 == 1 and report.mrr == 1 and report.critical_pass_rate == 1
    assert report.measured_thresholds_passed
    assert report.activation_allowed is False and report.labels_approved is False
    assert report.answer_groundedness is None
    assert report.agent_tool_evaluation == "labels_only_no_agent_runtime"


def test_golden_thresholds_and_positive_labels_are_bound(sources, config, embedding_config):
    index = candidate(sources, config, embedding_config)
    golden = fixture_golden(index)
    v = golden.model_dump(mode="json")
    v["thresholds"]["recall_at_5_min"] = 0.1
    with pytest.raises(ValidationError):
        GoldenSet.model_validate_json(json.dumps(v))
    v = golden.model_dump(mode="json")
    v["cases"][0]["expected_sections"][0]["heading_path"] = ["Missing section"]
    v.pop("golden_set_id")
    v["golden_set_id"] = "golden-set-sha256-" + canonical_sha256(v)
    wrong = GoldenSet.model_validate_json(json.dumps(v))
    with pytest.raises(ValueError, match="golden_label_section_missing"):
        validate_labels(index, wrong, retrieval_config())


@pytest.mark.parametrize("mutation", ["path", "heading_path", "document_status"])
def test_forbidden_labels_are_bound_to_candidate_sections_before_any_retrieval(
    sources, config, embedding_config, mutation, monkeypatch
):
    index = candidate(sources, config, embedding_config)
    v = fixture_golden(index).model_dump(mode="json")
    second = index.chunks.chunks[-1]
    forbidden = {
        "repository": second.repository,
        "path": second.path,
        "heading_path": [h.title for h in second.heading_path],
        "document_status": second.document_status,
    }
    v["cases"][0]["forbidden_sources"] = [forbidden]
    v.pop("golden_set_id")
    v["golden_set_id"] = "golden-set-sha256-" + canonical_sha256(v)
    correct = GoldenSet.model_validate_json(json.dumps(v))
    validate_labels(index, correct, retrieval_config())
    if mutation == "path":
        forbidden["path"] = "docs/missing.md"
    elif mutation == "heading_path":
        forbidden["heading_path"] = ["Removed section"]
    else:
        forbidden["document_status"] = "historical"
    v.pop("golden_set_id")
    v["golden_set_id"] = "golden-set-sha256-" + canonical_sha256(v)
    wrong = GoldenSet.model_validate_json(json.dumps(v))
    calls = []
    monkeypatch.setattr(
        "retailops_ai.pipelines.golden.search_candidate", lambda *a: calls.append(a)
    )
    with pytest.raises(ValueError, match="golden_forbidden_section_missing"):
        evaluate(index, wrong, retrieval_config())
    assert not calls


@pytest.mark.parametrize("family", ["expected_sections", "forbidden_sources"])
def test_duplicate_source_labels_are_rejected(sources, config, embedding_config, family):
    index = candidate(sources, config, embedding_config)
    v = fixture_golden(index).model_dump(mode="json")
    label = deepcopy(v["cases"][0]["expected_sections"][0])
    if family == "forbidden_sources":
        other = index.chunks.chunks[-1]
        label.update(
            repository=other.repository,
            path=other.path,
            heading_path=[h.title for h in other.heading_path],
        )
    v["cases"][0][family] = [label, deepcopy(label)]
    v.pop("golden_set_id")
    v["golden_set_id"] = "golden-set-sha256-" + canonical_sha256(v)
    with pytest.raises(ValidationError, match="golden_source_labels_duplicate"):
        GoldenSet.model_validate_json(json.dumps(v))


def test_committed_golden_and_retrieval_schemas_are_consistent():
    golden = load_golden_set(ROOT / "knowledge/golden.v1.json")
    assert 30 <= len(golden.cases) <= 50
    assert golden.review_state == "proposed"
    assert golden.retrieval_config_id == retrieval_config().config_id()
    for name, value in [
        ("golden-set", golden.model_dump(mode="json")),
        ("retrieval-request", request().model_dump(mode="json")),
    ]:
        schema = json.loads((ROOT / f"contracts/knowledge/v1/{name}.v1.schema.json").read_text())
        Draft202012Validator(schema).validate(value)


def test_offline_evaluation_cli_is_atomic_redacted_and_independent_of_settings(
    tmp_path, monkeypatch, capsys, sources, config, embedding_config
):
    index = candidate(sources, config, embedding_config)
    golden = fixture_golden(index)
    source = tmp_path / "candidate.json"
    labels = tmp_path / "golden.json"
    output = tmp_path / "report.json"
    source.write_text(index.model_dump_json())
    labels.write_text(golden.model_dump_json())
    monkeypatch.setenv("APP_ENV", "invalid-offline-marker")
    arguments = [
        "knowledge-evaluate",
        "--candidate",
        str(source),
        "--golden-set",
        str(labels),
        "--output",
        str(output),
    ]
    assert main(arguments) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["activation_allowed"] is False
    assert output.stat().st_mode & 0o777 == 0o600
    raw = output.read_bytes()
    assert main(arguments) == 2
    assert output.read_bytes() == raw
    captured = capsys.readouterr()
    assert "Body 0." not in captured.err and str(tmp_path) not in captured.err
