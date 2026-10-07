"""Byte and validation-error compatibility with canonical JSON v1 before optimization."""

import json
import math
from collections import UserDict
from collections.abc import Mapping
from decimal import Decimal

import pytest

from retailops_ai.data_contracts.identity import canonical_bytes


def legacy_finite(value):
    # Frozen pre-optimization validator: an independent compatibility oracle.
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite_json_number")
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("json_keys_must_be_strings")
        for item in value.values():
            legacy_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            legacy_finite(item)


def legacy_bytes(value):
    legacy_finite(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


class Text(str):
    pass


class Integer(int):
    pass


class Floating(float):
    pass


class Array(list):
    pass


class Tuple(tuple):
    pass


class Object(dict):
    pass


def outcome(function, value):
    try:
        return ("bytes", function(value))
    except (ValueError, TypeError) as error:
        return (type(error), str(error))


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        2**100,
        -0.0,
        0.0,
        1.25,
        1e-300,
        'zażółć / 雪 / \n / "',
        {},
        [],
        (),
        {"z": [None, True, 7, -0.0], "a": ({"text": "é"},)},
        Text("text"),
        Integer(9),
        Floating(-0.0),
        Array([Integer(5), Floating(1.5), Text("é")]),
        Tuple((None, Object({Text("key"): Array([False])}))),
        Object({"nested": Tuple((1, 2))}),
        UserDict({"valid": [1, 2]}),
        b"unsupported",
        {1, 2},
        Decimal("1.5"),
        complex(1, 2),
        {1: "invalid key"},
        {"x": 0, None: "invalid key"},
        {"x": {False: 0}},
        # Key validation precedes values within a mapping, even when the
        # nonfinite value is first. Container traversal retains its order.
        {"first": float("nan"), 1: 0},
        {"first": {1: 0}, "second": float("nan")},
        {"first": float("nan"), "second": {1: 0}},
        UserDict({"first": float("inf"), 1: 0}),
        Object({"first": float("nan"), 1: 0}),
    ],
)
def test_canonical_bytes_and_errors_match_original(value):
    assert outcome(canonical_bytes, value) == outcome(legacy_bytes, value)


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("number_type", [float, Floating])
@pytest.mark.parametrize("container", [dict, Object, UserDict, list, Array, tuple, Tuple])
def test_nested_nonfinite_numbers_keep_the_original_error(number, number_type, container):
    item = {"inner": [({"number": number_type(number)},)]}
    value = container({"outer": item}) if issubclass(container, Mapping) else container([item])
    expected = (ValueError, "nonfinite_json_number")
    assert outcome(legacy_bytes, value) == expected
    assert outcome(canonical_bytes, value) == expected


def test_custom_mapping_validation_order_matches_original():
    class TracedMapping(Mapping):
        def __init__(self, data):
            self.data = data
            self.events = []

        def __iter__(self):
            self.events.append("keys")
            return iter(self.data)

        def __len__(self):
            return len(self.data)

        def __getitem__(self, key):
            self.events.append(key)
            return self.data[key]

    for data in ({"a": 1, "b": float("nan")}, {"a": float("nan"), 1: 0}, {"a": 1}):
        before, after = TracedMapping(data), TracedMapping(data)
        assert outcome(canonical_bytes, after) == outcome(legacy_bytes, before)
        assert after.events == before.events


def test_canonical_wire_format_stays_exact():
    assert canonical_bytes({"z": -0.0, "a": [None, True, 1, "é"]}) == (
        '{"a":[null,true,1,"é"],"z":-0.0}'.encode()
    )
