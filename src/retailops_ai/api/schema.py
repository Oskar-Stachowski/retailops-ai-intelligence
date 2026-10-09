"""Separate diagnostic/access snapshots without duplicating their executable contracts."""

from copy import deepcopy
from typing import Any


def contract_openapi(
    document: dict[str, Any], *, access: bool, assistant: bool = False
) -> dict[str, Any]:
    result = deepcopy(document)
    paths = {
        path: spec
        for path, spec in result["paths"].items()
        if path.startswith("/api/v1/") == access
        and (
            not access
            or (
                path.startswith("/api/v1/assistant/")
                or path == "/api/v1/recommendations"
                or path.startswith("/api/v1/recommendations/")
            )
            == assistant
        )
    }
    result["paths"] = paths
    components = result.get("components", {})
    all_schemas = components.get("schemas", {})
    used: set[str] = set()

    def references(value: object) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            prefix = "#/components/schemas/"
            if isinstance(reference, str) and reference.startswith(prefix):
                name = reference[len(prefix) :]
                if name not in used:
                    used.add(name)
                    references(all_schemas[name])
            for item in value.values():
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)

    references(paths)
    components["schemas"] = {name: spec for name, spec in all_schemas.items() if name in used}
    scheme = "apiBearer" if access else "metricsBearer"
    components["securitySchemes"] = {scheme: components["securitySchemes"][scheme]}
    if access:
        result["info"]["title"] = (
            "RetailOps AI Assistant" if assistant else "RetailOps AI local access"
        )
    return result
