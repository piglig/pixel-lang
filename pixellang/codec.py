"""Versioned RGBA physical encoding. No language parsing belongs here."""

import json
import math
import struct
from pathlib import Path

from .model import VERSIONS, PixelError, Span, Token
from .typesys import BUILTINS

OPS = ["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=", "and", "or", "not"]
KEYWORDS = [
    "let",
    "set",
    "if",
    "else",
    "while",
    "fn",
    "return",
    "print",
    "import",
    "export",
    "do",
    "try",
    "catch",
    "for",
    "in",
    "record",
    "var",
    "entry",
    "break",
    "continue",
]
KEYWORDS += ["enum", "variant", "match", "case", "otherwise", "lambda", "lambda_body"]
PUNCT = ["(", ")", ",", ":", "=", "->", ".", "[", "]", "{", "}", "@"]
PUNCT += ["+=", "-=", "*=", "/=", "%="]
PUNCT += ["=>"]
TYPES = ["int", "bool", "str", "unit", "map", "record", "json", "Error"]
TYPES += ["float64"]
TYPES += ["parameter"]
TYPES += ["Option", "function"]
TABLES = {
    48: ("op", OPS),
    64: ("keyword", KEYWORDS),
    80: ("punct", PUNCT),
    96: ("type", TYPES),
    128: ("builtin", BUILTINS),
}
MAX_PIXELS = 1_000_000


def rgba(kind, value):
    if kind == "int":
        if type(value) is not int or not -32768 <= value <= 32767:
            raise PixelError("encoding", "Integer literal must fit signed 16-bit")
        r, n = 16, value & 65535
    elif kind == "bool":
        if type(value) is not bool:
            raise PixelError("encoding", "Boolean payload must be true or false")
        r, n = 17, int(value)
    elif kind == "id":
        if type(value) is not int or not 0 <= value <= 65535:
            raise PixelError("encoding", "Identifier must be 0..65535")
        r, n = 32, value
    elif kind in ("word", "string_head", "integer_head", "float_head"):
        if type(value) is not int or not 0 <= value <= 65535:
            raise PixelError("encoding", "Invalid literal word")
        r, n = (
            {"word": 112, "string_head": 19, "integer_head": 18, "float_head": 20}[
                kind
            ],
            value,
        )
    else:
        entries = [(r, values) for r, (k, values) in TABLES.items() if k == kind]
        if not entries or value not in entries[0][1]:
            raise PixelError("encoding", f"Unknown semantic token {kind}:{value}")
        r, values = entries[0]
        n = values.index(value)
    return [r, n >> 8, n & 255, 255]


def decode_pixel(color, position, source):
    span = Span(source, (tuple(position),))
    if (
        not isinstance(color, list)
        or len(color) != 4
        or any(type(v) is not int or not 0 <= v <= 255 for v in color)
    ):
        raise PixelError("encoding", "RGBA must contain four bytes", span)
    r, g, b, a = color
    n = (g << 8) | b
    if a == 0:
        return None
    if a != 255:
        raise PixelError(
            "encoding", "Semantic pixels require alpha 255; empty pixels alpha 0", span
        )
    if r == 16:
        kind, value = "int", n - 65536 if n >= 32768 else n
    elif r == 17 and n < 2:
        kind, value = "bool", bool(n)
    elif r == 32:
        kind, value = "id", n
    elif r in (18, 19, 20, 112):
        kind, value = (
            {18: "integer_head", 19: "string_head", 20: "float_head", 112: "word"}[r],
            n,
        )
    elif r in TABLES and n < len(TABLES[r][1]):
        kind, values = TABLES[r]
        value = values[n]
    else:
        raise PixelError("encoding", f"Unknown RGBA semantic encoding {color}", span)
    return Token(kind, value, tuple(position), span)


def new_source(width, height, pixels=None, links=None):
    return {
        "magic": "PIXELLANG",
        "versions": dict(VERSIONS),
        "encoding": "rgba8",
        "dimensions": [width, height],
        "pixels": pixels or [],
        "spatial": {"links": links or []},
        "metadata": {},
    }


def validate(doc, source="<source>"):
    if not isinstance(doc, dict):
        raise PixelError("format", "Source must be an object")
    if (
        doc.get("magic") != "PIXELLANG"
        or doc.get("versions") != VERSIONS
        or doc.get("encoding") != "rgba8"
    ):
        raise PixelError("format", "Unsupported magic, version axes or encoding")
    if not isinstance(doc.get("metadata", {}), dict):
        raise PixelError("format", "Metadata must be an object")
    if "bundle" in doc and (
        not isinstance(doc["bundle"], dict)
        or doc["bundle"].get("version") != "0.8"
        or not isinstance(doc["bundle"].get("modules", {}), dict)
    ):
        raise PixelError("format", "Invalid bundled modules")
    dims = doc.get("dimensions")
    if (
        not isinstance(dims, list)
        or len(dims) != 2
        or any(type(v) is not int or v < 1 for v in dims)
        or dims[0] * dims[1] > MAX_PIXELS
    ):
        raise PixelError(
            "format", "Expected positive 2D dimensions with at most 1,000,000 cells"
        )
    pixels = doc.get("pixels")
    if not isinstance(pixels, list) or len(pixels) > MAX_PIXELS:
        raise PixelError("format", "Invalid sparse pixel list")
    seen, tokens = set(), []
    for p in pixels:
        if not isinstance(p, dict):
            raise PixelError("format", "Pixel must be an object")
        pos = p.get("position")
        if (
            not isinstance(pos, list)
            or len(pos) != 2
            or any(
                type(v) is not int or v < 0 or v >= dims[i] for i, v in enumerate(pos)
            )
        ):
            raise PixelError("format", f"Invalid pixel position {pos}")
        if tuple(pos) in seen:
            raise PixelError(
                "format", "Duplicate pixel position", Span(source, (tuple(pos),))
            )
        seen.add(tuple(pos))
        t = decode_pixel(p.get("rgba"), pos, source)
        if t:
            tokens.append(t)
    spatial = doc.get("spatial", {})
    if not isinstance(spatial, dict) or not isinstance(spatial.get("links", []), list):
        raise PixelError("format", "Invalid spatial links")
    for link in spatial.get("links", []):
        if not isinstance(link, dict) or link.get("kind") not in ("next", "child", "region"):
            raise PixelError("spatial", "Unknown connection kind")
        for key in ("from", "to"):
            p = link.get(key)
            if (
                not isinstance(p, list)
                or len(p) != 2
                or any(type(v) is not int for v in p)
            ):
                raise PixelError(
                    "spatial", "Connection endpoints must be 2D coordinates"
                )
    return sorted(tokens, key=lambda t: (t.position[1], t.position[0]))


def load(path):
    path = Path(path)
    try:
        if path.suffix.lower() == ".png":
            from PIL import Image

            with Image.open(path) as im:
                from .picture import MAX_IMAGE, decode_picture, is_picture

                if im.width * im.height > MAX_IMAGE:
                    raise PixelError("format", "Image exceeds physical pixel limit")
                if is_picture(im):
                    return decode_picture(im)
                if im.width * im.height > MAX_PIXELS:
                    raise PixelError("format", "Image exceeds cell limit")
                extension = json.loads(im.info.get("pixellang", "{}"))
                if extension and (
                    not isinstance(extension, dict)
                    or extension.get("versions") != VERSIONS
                ):
                    raise PixelError("format", "Unsupported PNG spatial metadata")
                im = im.convert("RGBA")
                pixels = [
                    {"position": [x, y], "rgba": list(im.getpixel((x, y)))}
                    for y in range(im.height)
                    for x in range(im.width)
                    if im.getpixel((x, y))[3]
                ]
                doc = new_source(
                    im.width,
                    im.height,
                    pixels,
                    extension.get("links", []),
                )
        else:
            if path.stat().st_size > 64_000_000:
                raise PixelError("format", "Source file too large")
            doc = json.loads(path.read_text())
        validate(doc, str(path))
        return doc
    except PixelError:
        raise
    except (OSError, ValueError, TypeError, AttributeError) as e:
        raise PixelError("format", f"Cannot load {path}: {e}") from e


def save(doc, path):
    validate(doc)
    path = Path(path)
    if path.suffix.lower() == ".png" and doc.get("bundle"):
        from .picture import save_picture

        save_picture(doc, path)
    elif path.suffix.lower() == ".png":
        from PIL import Image, PngImagePlugin

        im = Image.new("RGBA", tuple(doc["dimensions"]), (0, 0, 0, 0))
        for p in doc["pixels"]:
            im.putpixel(tuple(p["position"]), tuple(p["rgba"]))
        info = PngImagePlugin.PngInfo()
        info.add_text(
            "pixellang",
            json.dumps(
                {
                    "versions": doc["versions"],
                    "links": doc.get("spatial", {}).get("links", []),
                },
                sort_keys=True,
            ),
        )
        im.save(path, pnginfo=info)
    else:
        path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def palette():
    return [
        {"kind": k, "value": v, "rgba": rgba(k, v)}
        for k, vs in [("int", [0, 10, 20]), ("bool", [False, True]), ("id", [0, 1, 2])]
        + [(k, vs) for k, vs in TABLES.values()]
        for v in vs
    ]


def encode_token(kind, value):
    if kind == "float64":
        if type(value) is not float or not math.isfinite(value):
            raise PixelError("encoding", "Float64 literal must be finite")
        data = struct.pack(">d", value)
        return [rgba("float_head", 0)] + [
            rgba("word", int.from_bytes(data[i : i + 2], "big")) for i in range(0, 8, 2)
        ]
    if kind == "str":
        data = value.encode("utf-8")
        if len(data) > 65535:
            raise PixelError("encoding", "String literal exceeds 65535 UTF-8 bytes")
        colors = [rgba("string_head", len(data))]
        data += b"\x00" * (len(data) % 2)
        return colors + [
            rgba("word", int.from_bytes(data[i : i + 2], "big"))
            for i in range(0, len(data), 2)
        ]
    if kind == "int" and not -32768 <= value <= 32767:
        if type(value) is not int or not -(2**63) <= value < 2**63:
            raise PixelError("encoding", "Integer literal exceeds signed 64-bit range")
        data = value.to_bytes(8, "big", signed=True)
        return [rgba("integer_head", 0)] + [
            rgba("word", int.from_bytes(data[i : i + 2], "big")) for i in range(0, 8, 2)
        ]
    return [rgba(kind, value)]
