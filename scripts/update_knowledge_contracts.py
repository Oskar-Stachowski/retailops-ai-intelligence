"""Generate/check knowledge schemas and the registered candidate's structure."""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from retailops_ai.data_contracts.run import KnowledgeRunRecord
from retailops_ai.knowledge.chunks import ChunkerConfig, ChunkManifest
from retailops_ai.knowledge.contracts import CorpusManifest, CorpusRegistry
from retailops_ai.knowledge.golden import GoldenReport, GoldenSet
from retailops_ai.knowledge.indexes import EmbeddingConfig, IndexCandidate, IndexManifest
from retailops_ai.knowledge.jobs import (
    CurrentKnowledgeIndex,
    IndexBuildProfile,
    IndexRunReport,
    KnowledgeIndexRequest,
)
from retailops_ai.knowledge.releases import (
    CorpusApproval,
    IndexPin,
    IndexValidation,
    SwitchRequest,
    SwitchResult,
)
from retailops_ai.knowledge.retrieval import (
    DocumentDenial,
    RetrievalConfig,
    RetrievalRequest,
    RetrievalResult,
)
from retailops_ai.knowledge.review import SimilarityPolicy, SimilarityReport
from retailops_ai.pipelines.chunks import load_chunker_config
from retailops_ai.pipelines.corpus import load_registry
from retailops_ai.pipelines.golden import load_golden_set
from retailops_ai.pipelines.indexes import load_embedding_config
from retailops_ai.pipelines.retrieval import load_retrieval_config
from retailops_ai.pipelines.review import load_similarity_policy

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    models: dict[str, type[BaseModel]] = {
        "corpus-registry": CorpusRegistry,
        "corpus-manifest": CorpusManifest,
        "chunker-config": ChunkerConfig,
        "chunk-manifest": ChunkManifest,
        "embedding-config": EmbeddingConfig,
        "index-manifest": IndexManifest,
        "index-candidate": IndexCandidate,
        "corpus-approval": CorpusApproval,
        "index-validation": IndexValidation,
        "index-pin": IndexPin,
        "switch-request": SwitchRequest,
        "switch-result": SwitchResult,
        "retrieval-config": RetrievalConfig,
        "retrieval-request": RetrievalRequest,
        "retrieval-result": RetrievalResult,
        "document-denial": DocumentDenial,
        "golden-set": GoldenSet,
        "golden-report": GoldenReport,
        "index-build-profile": IndexBuildProfile,
        "index-run-report": IndexRunReport,
        "knowledge-index-request": KnowledgeIndexRequest,
        "knowledge-index-run": KnowledgeRunRecord,
        "current-knowledge-index": CurrentKnowledgeIndex,
        "similarity-policy": SimilarityPolicy,
        "similarity-report": SimilarityReport,
    }
    for name, model in models.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:knowledge:{name}:1.0"
        text = json.dumps(schema, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        path = ROOT / "contracts/knowledge/v1" / (name + ".v1.schema.json")
        if args.check:
            if not path.is_file() or path.read_text() != text:
                stale.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    load_registry(ROOT / "knowledge/corpus.v1.json")
    load_chunker_config(ROOT / "knowledge/chunker.v1.json")
    load_embedding_config(ROOT / "knowledge/embeddings.fake.v1.json")
    load_similarity_policy(ROOT / "knowledge/similarity.v1.json")
    config = load_retrieval_config(ROOT / "src/retailops_ai/knowledge/retrieval.default.json")
    golden = load_golden_set(ROOT / "knowledge/golden.v1.json")
    if golden.retrieval_config_id != config.config_id():
        raise ValueError("golden_retrieval_configuration_mismatch")
    if stale:
        print("Knowledge snapshots differ: " + ", ".join(sorted(stale)))
        return 1
    print("Knowledge schemas, candidate registry and chunker config checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
