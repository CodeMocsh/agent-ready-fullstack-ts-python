"""Every successful answer of every route has a largest size, worked out from its schema before
the app runs, and that size fits the default limit of a response. `docs/adr/template/0028`."""

import json
from collections.abc import Iterator
from typing import Any, Final

from app.main import create_app
from app.models import MAX_BYTES
from app.response_limits import DEFAULT_BYTES

BOUNDED_ELSEWHERE: Final[dict[str, str]] = {
    "GET /reads": "app.reads.answer leaves out each part that would pass the deployment ceiling",
}
"""Each route whose schema gives its answer no largest size, and the code that bounds it instead.
A route leaves this list in the change that bounds its schema. A route on the list whose schema
bounds it fails, so a route never stays here by accident."""

NUMBER_BYTES: Final = 24
"""The most bytes a JSON number takes: a 64-bit integer, or a float as Python writes one."""

CHARACTER_BYTES: Final = 6
"""The most bytes one character of a JSON string takes: `\\u001f`."""

FIXED_FORMATS: Final = {"uuid": 38, "date-time": 40, "date": 12}
"""The most bytes a string of each format takes, quotes included."""


class Unbounded(Exception):
    """A part of a schema has no largest size. `where` is its path in the answer."""

    def __init__(self, where: str, why: str) -> None:
        super().__init__(f"{where} {why}")


_SCALARS: Final = {"null": 4, "boolean": 5, "integer": NUMBER_BYTES, "number": NUMBER_BYTES}
"""The most bytes a JSON value of each type that holds nothing else takes."""

Schemas = dict[str, Any]


def largest(schema: Schemas, schemas: Schemas, where: str, seen: frozenset[str]) -> int:
    """The most bytes a JSON value that `schema` describes takes. `schemas` holds each schema it
    may refer to, and `seen` each one it is already inside. Raises `Unbounded`."""
    if MAX_BYTES in schema:
        return int(schema[MAX_BYTES])
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in seen:
            raise Unbounded(where, f"refers to {name} inside itself")
        return largest(schemas[name], schemas, where, seen | {name})
    branches = schema.get("anyOf", schema.get("oneOf"))
    if branches is not None:
        return max(largest(branch, schemas, where, seen) for branch in branches)
    listed = [schema["const"]] if "const" in schema else schema.get("enum")
    if listed is not None:
        return max(len(json.dumps(value).encode()) for value in listed)
    kind = schema.get("type")
    if kind in _SCALARS:
        return _SCALARS[kind]
    if kind not in _HOLDERS:
        raise Unbounded(where, "may hold any JSON value")
    return _HOLDERS[kind](schema, schemas, where, seen)


def _string(schema: Schemas, _schemas: Schemas, where: str, _seen: frozenset[str]) -> int:
    if "maxLength" in schema:
        return 2 + CHARACTER_BYTES * int(schema["maxLength"])
    if schema.get("format") in FIXED_FORMATS:
        return FIXED_FORMATS[schema["format"]]
    raise Unbounded(where, "is a string with no maxLength")


def _array(schema: Schemas, schemas: Schemas, where: str, seen: frozenset[str]) -> int:
    if "maxItems" not in schema:
        raise Unbounded(where, "is an array with no maxItems")
    item = largest(schema.get("items", {}), schemas, f"{where}[]", seen)
    return 2 + int(schema["maxItems"]) * (item + 1)


def _object(schema: Schemas, schemas: Schemas, where: str, seen: frozenset[str]) -> int:
    size = 2 + sum(
        len(json.dumps(name).encode()) + 2 + largest(value, schemas, f"{where}.{name}", seen)
        for name, value in schema.get("properties", {}).items()
    )
    more = schema.get("additionalProperties", False)
    if more is False:
        return size
    if "maxProperties" not in schema:
        raise Unbounded(where, "holds any number of keys, with no maxProperties")
    names = schema.get("propertyNames", {"type": "string"})
    key = _string(names, schemas, f"{where} key", seen)
    value = largest(more if isinstance(more, dict) else {}, schemas, f"{where}.*", seen)
    return size + int(schema["maxProperties"]) * (key + 2 + value)


_HOLDERS: Final = {"string": _string, "array": _array, "object": _object}
"""How to work out the most bytes a value of each type that holds other values takes."""


def answers() -> Iterator[tuple[str, Schemas, Schemas]]:
    """Each route, as `METHOD /path`, with the schema of its successful answer and the schemas
    that answer refers to."""
    spec = create_app().openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            for status, answer in operation.get("responses", {}).items():
                schema = answer.get("content", {}).get("application/json", {}).get("schema")
                if status.startswith("2") and schema is not None:
                    yield f"{method.upper()} {path}", schema, schemas


def bounded(schema: Schemas, schemas: Schemas) -> bool:
    try:
        largest(schema, schemas, "$", frozenset())
    except Unbounded:
        return False
    return True


def test_every_answer_has_a_largest_size_that_fits_the_default_limit() -> None:
    over: list[str] = []
    for route, schema, schemas in answers():
        if route in BOUNDED_ELSEWHERE:
            continue
        try:
            size = largest(schema, schemas, "$", frozenset())
        except Unbounded as unbounded:
            over.append(f"{route}: {unbounded}")
            continue
        if size > DEFAULT_BYTES:
            over.append(f"{route}: answers up to {size} bytes, over {DEFAULT_BYTES}")

    assert over == [], (
        "Bound each part of these answers in its model: max_length on a str or a list, or "
        "MAX_BYTES on a value that is any JSON.\n" + "\n".join(over)
    )


def test_every_route_bounded_elsewhere_has_a_schema_that_cannot_bound_it() -> None:
    routes = {route: (schema, schemas) for route, schema, schemas in answers()}

    assert [route for route in BOUNDED_ELSEWHERE if route not in routes] == []
    assert [route for route in BOUNDED_ELSEWHERE if bounded(*routes[route])] == []
