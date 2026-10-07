"""Reject overlays that expose databases, fork the broker or auto-run delivery."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest


def validator():
    path = Path(__file__).resolve().parents[1] / "scripts/check_ai10_compose_overlays.py"
    spec = importlib.util.spec_from_file_location("ai10_overlay_validation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pair():
    bus = {"external": True, "name": "ai10-source-owned-config-validation"}
    ai = {
        "networks": {"intelligence_bus": deepcopy(bus)},
        "services": {
            **{
                name: {"networks": {"ai_backend": {}}}
                for name in ("db", "mlflow", "api-migrate", "mlflow-migrate")
            },
            "api": {"networks": {"intelligence_bus": {"aliases": ["retailops-ai"]}}},
            "intelligence-delivery": {
                "networks": {"ai_backend": {}, "intelligence_bus": {}},
                "profiles": ["intelligence"],
                "restart": "no",
                "volumes": [{"target": "/private", "read_only": True}],
            },
        },
    }
    source = {
        "networks": {"intelligence_bus": deepcopy(bus)},
        "services": {
            "db": {"networks": {"default": {}}},
            "api": {
                "networks": {"default": {}, "intelligence_bus": {"aliases": ["retailops-api"]}}
            },
            "redpanda": {
                "networks": {"default": {}, "intelligence_bus": {}},
                "command": [
                    "--advertise-kafka-addr",
                    "internal://redpanda:9092,external://localhost:29092",
                ],
            },
        },
    }
    return ai, source


def test_two_projects_keep_private_databases_and_one_reachable_source_broker():
    validator().validate(*pair())


@pytest.mark.parametrize("side", (0, 1))
def test_cross_project_database_access_is_rejected(side):
    configs = pair()
    configs[side]["services"]["db"]["networks"]["intelligence_bus"] = {}
    with pytest.raises(ValueError, match="database_must_remain"):
        validator().validate(*configs)


@pytest.mark.parametrize("side", (0, 1))
def test_cleanup_cannot_own_or_remove_the_other_projects_network(side):
    configs = pair()
    configs[side]["networks"]["intelligence_bus"]["external"] = False
    with pytest.raises(ValueError, match="source_owned_external"):
        validator().validate(*configs)


def test_mismatched_external_network_names_are_rejected():
    ai, source = pair()
    source["networks"]["intelligence_bus"]["name"] = "different-source-bus"
    with pytest.raises(ValueError, match="source_owned_external"):
        validator().validate(ai, source)


def test_second_ai_broker_is_rejected():
    ai, source = pair()
    ai["services"]["redpanda"] = {"networks": {"intelligence_bus": {}}}
    with pytest.raises(ValueError, match="second_broker"):
        validator().validate(ai, source)


def test_broker_metadata_cannot_advertise_an_unreachable_host():
    ai, source = pair()
    source["services"]["redpanda"]["command"][-1] = "internal://localhost:9092"
    with pytest.raises(ValueError, match="advertised_listener"):
        validator().validate(ai, source)


def test_shared_service_name_cannot_replace_the_explicit_source_api_alias():
    ai, source = pair()
    source["services"]["api"]["networks"]["intelligence_bus"]["aliases"] = ["api"]
    with pytest.raises(ValueError, match="explicit_cross_project_api_alias"):
        validator().validate(ai, source)


def test_private_delivery_configuration_cannot_be_writable():
    ai, source = pair()
    ai["services"]["intelligence-delivery"]["volumes"][0]["read_only"] = False
    with pytest.raises(ValueError, match="read_only_mount"):
        validator().validate(ai, source)


def test_normal_compose_up_cannot_start_delivery_automatically():
    ai, source = pair()
    ai["services"]["intelligence-delivery"]["profiles"] = []
    with pytest.raises(ValueError, match="explicit_bounded_operation"):
        validator().validate(ai, source)
