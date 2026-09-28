"""Generate/check knowledge schemas and the registered candidate's structure."""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from retailops_ai.knowledge.chunks import ChunkerConfig, ChunkManifest
from retailops_ai.knowledge.contracts import CorpusManifest, CorpusRegistry
from retailops_ai.pipelines.chunks import load_chunker_config
from retailops_ai.pipelines.corpus import load_registry

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
    if stale:
        print("Knowledge snapshots differ: " + ", ".join(sorted(stale)))
        return 1
    print("Knowledge schemas, candidate registry and chunker config checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
