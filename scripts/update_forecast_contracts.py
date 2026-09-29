"""Generate or check AI 04.1 schemas and the resolved, packaged task policy."""

import argparse
import json
from pathlib import Path

from retailops_ai.forecasting.contract import CalendarManifest, TaskConfig

ROOT = Path(__file__).resolve().parents[1]


def artifacts() -> dict[Path, object]:
    result: dict[Path, object] = {}
    for name, model in (("task", TaskConfig), ("calendar_manifest", CalendarManifest)):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:forecast:{name}:1.0.0"
        result[ROOT / f"contracts/forecast/v1/{name}.schema.json"] = schema
    result[ROOT / "src/retailops_ai/forecasting/task.default.json"] = TaskConfig().model_dump(
        mode="json"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, value in artifacts().items():
        raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    if stale:
        print("Forecast contracts differ: " + ", ".join(stale))
        return 1
    print("Forecast contract snapshots checked." if args.check else "Forecast contracts generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
