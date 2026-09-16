"""Immutable tagged JSON values, independent of mutable VM references."""

import json
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .model import PixelError

MAX_JSON_SIZE = 1_000_000
MAX_JSON_NODES = 100_000
MAX_JSON_DEPTH = 128


def error(message, code="json.invalid_value", **details):
    raise PixelError("data", message[:256], code=code, **details)


def field_path(path, key):
    return path + (
        "." + key
        if key.isidentifier()
        else "[" + json.dumps(key, ensure_ascii=False) + "]"
    )


@dataclass(frozen=True)
class JsonValue:
    tag: str
    data: object = None

    def __deepcopy__(self, memo):
        # Every payload is a scalar or tuple of other immutable tagged values.
        return self


@dataclass(frozen=True)
class Number:
    spelling: str


def parse(text):
    if len(text) > MAX_JSON_SIZE:
        error("JSON input exceeds one million characters")

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                error("Duplicate JSON object key: " + key[:100], "json.duplicate_key")
            result[key] = value
        return result

    def constant(value):
        error("Non-finite JSON numbers are not supported", "json.invalid_syntax")

    try:
        raw = json.loads(
            text,
            parse_int=Number,
            parse_float=Number,
            parse_constant=constant,
            object_pairs_hook=pairs,
        )
    except (ValueError, RecursionError) as exc:
        error("Invalid JSON: " + str(exc), "json.invalid_syntax")
    return from_host(raw)


def from_host(value):
    count = 0

    def visit(item, depth=0):
        nonlocal count
        count += 1
        if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            error("JSON structure budget exceeded")
        if isinstance(item, JsonValue):
            return item
        if item is None:
            return JsonValue("null")
        if type(item) is bool:
            return JsonValue("bool", item)
        if isinstance(item, Number):
            return JsonValue("number", item.spelling)
        if type(item) is int:
            return JsonValue("number", str(item))
        if type(item) is float:
            if not math.isfinite(item):
                error("JSON float64 must be finite")
            return JsonValue("number", repr(item))
        if type(item) is str:
            try:
                item.encode("utf-8")
            except UnicodeError:
                error("JSON strings require Unicode scalar values")
            return JsonValue("string", item)
        if isinstance(item, (list, tuple)):
            return JsonValue("array", tuple(visit(child, depth + 1) for child in item))
        if isinstance(item, dict):
            fields = []
            for key, child in item.items():
                if type(key) is not str:
                    error("JSON object keys must be strings")
                # Object names label members; they are not additional values.
                # Match stringify's node/depth accounting so its output can be
                # parsed under the same limits. Names still require valid Unicode.
                try:
                    key.encode("utf-8")
                except UnicodeError:
                    error("JSON strings require Unicode scalar values")
                fields.append((key, visit(child, depth + 1)))
            return JsonValue("object", tuple(fields))
        error("Value cannot be represented as JSON")

    return visit(value)


def stringify(value):
    pieces, size, count = [], 0, 0

    def emit(piece):
        nonlocal size
        size += len(piece)
        if size > MAX_JSON_SIZE:
            error("JSON output exceeds one million characters")
        pieces.append(piece)

    def visit(item, depth=0):
        nonlocal count
        count += 1
        if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            error("JSON structure budget exceeded")
        if item.tag == "null":
            emit("null")
        elif item.tag == "bool":
            emit("true" if item.data else "false")
        elif item.tag == "number":
            emit(item.data)
        elif item.tag == "string":
            emit(json.dumps(item.data, ensure_ascii=False))
        elif item.tag == "array":
            emit("[")
            for i, child in enumerate(item.data):
                if i:
                    emit(",")
                visit(child, depth + 1)
            emit("]")
        elif item.tag == "object":
            emit("{")
            for i, (key, child) in enumerate(item.data):
                if i:
                    emit(",")
                emit(json.dumps(key, ensure_ascii=False))
                emit(":")
                visit(child, depth + 1)
            emit("}")
        else:
            error("Invalid JSON value tag")

    visit(value)
    return "".join(pieces)


def require(value, kind):
    if value.tag != kind:
        error(
            f"Expected JSON {kind}, got {value.tag}",
            "json.type_mismatch",
            expected=kind,
            actual=value.tag,
        )
    return value.data


def as_int(value):
    spelling = require(value, "number")
    try:
        number = Decimal(spelling)
        if (
            not number.is_finite()
            or number != number.to_integral_value()
            or not -(2**63) <= number < 2**63
        ):
            error(
                "JSON number is not an exact signed 64-bit integer",
                "json.numeric_range",
                expected="int",
                actual=spelling,
            )
        return int(number)
    except (InvalidOperation, ValueError, OverflowError):
        error(
            "JSON number is not an exact signed 64-bit integer",
            "json.numeric_range",
            expected="int",
            actual=spelling,
        )


def as_float64(value):
    spelling = require(value, "number")
    try:
        result = float(spelling)
    except (ValueError, OverflowError):
        error(
            "JSON number exceeds finite float64 range",
            "json.numeric_range",
            expected="float64",
            actual=spelling,
        )
    if not math.isfinite(result):
        error(
            "JSON number exceeds finite float64 range",
            "json.numeric_range",
            expected="float64",
            actual=spelling,
        )
    return result
