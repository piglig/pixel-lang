"""Physical-independent semantic and spatial data structures."""

from dataclasses import dataclass, field
from typing import Any

VERSIONS = {"language": "0.8", "encoding": "0.8", "format": "0.8"}


@dataclass(frozen=True)
class Span:
    source: str
    points: tuple[tuple[int, ...], ...]

    def to_dict(self):
        return {
            "source": self.source,
            "points": [list(p) for p in self.points],
            "min": list(map(min, zip(*self.points))) if self.points else [0, 0],
            "max": list(map(max, zip(*self.points))) if self.points else [0, 0],
        }

    @classmethod
    def merge(cls, *spans):
        valid = [s for s in spans if s]
        return (
            cls(valid[0].source, tuple(sorted({p for s in valid for p in s.points})))
            if valid
            else cls("<source>", ())
        )


class PixelError(Exception):
    def __init__(
        self,
        phase,
        message,
        span=None,
        *,
        code=None,
        path="",
        operation="",
        expected="",
        actual="",
    ):
        self.phase, self.message, self.span = phase, message, span
        self.code = code or phase + ".failure"
        self.path, self.operation = path[:1024], operation[:64]
        self.expected, self.actual = expected[:256], actual[:256]
        super().__init__(message)

    def fields(self):
        return {
            "kind": self.code.split(".", 1)[0],
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "operation": self.operation,
            "expected": self.expected,
            "actual": self.actual,
        }

    def to_dict(self):
        return {
            **{key: value for key, value in self.fields().items() if key != "kind"},
            "phase": self.phase,
            "span": self.span.to_dict() if isinstance(self.span, Span) else self.span,
        }

    def __str__(self):
        d = self.to_dict()["span"]
        loc = f" {d['source']}:{d['min']}–{d['max']}" if d else ""
        if d and isinstance(d.get("text"), dict):
            d = d["text"]
        if valid_text_span(d):
            loc = f" {d['source']}:{d['start']['line']}:{d['start']['column']}"
        return f"{self.phase}{loc}: {self.message}"


@dataclass(frozen=True)
class Token:
    kind: str
    value: Any
    position: tuple[int, ...]
    span: Span


@dataclass
class Region:
    id: int
    tokens: list[Token]
    indent: int
    span: Span
    parent: int | None = None
    explicit_parent: bool = False


@dataclass
class Node:
    kind: str
    data: dict = field(default_factory=dict)
    span: Span | None = None

    def to_dict(self, provenance=True):
        def conv(v):
            if isinstance(v, Node):
                return v.to_dict(provenance)
            if isinstance(v, Span):
                return v.to_dict()
            if isinstance(v, list):
                return [conv(x) for x in v]
            if isinstance(v, dict):
                return {
                    k: conv(x)
                    for k, x in v.items()
                    if provenance or not k.startswith("_")
                }
            return v

        result = {"kind": self.kind, **conv(self.data)}
        if provenance and self.span:
            result["span"] = self.span.to_dict()
        return result


@dataclass(frozen=True)
class TextSpan(Span):
    start: int = 0
    end: int = 0

    def to_dict(self):
        first, last = self.points[0], self.points[-1]
        return {
            **super().to_dict(),
            "kind": "text",
            "start": {
                "line": first[1] + 1,
                "column": first[0] + 1,
                "offset": self.start,
            },
            "end": {"line": last[1] + 1, "column": last[0] + 1, "offset": self.end},
        }


def valid_text_span(value):
    return (
        isinstance(value, dict)
        and value.get("kind") == "text"
        and isinstance(value.get("source"), str)
        and all(
            isinstance(value.get(k), dict)
            and all(
                type(value[k].get(n)) is int and value[k][n] >= minimum
                for n, minimum in (("line", 1), ("column", 1), ("offset", 0))
            )
            for k in ("start", "end")
        )
        and value["start"]["offset"] <= value["end"]["offset"]
    )
