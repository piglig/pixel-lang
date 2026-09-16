"""Executable, glyph-free image container. Program data lives in visible pixels."""

import copy
import json
import math
import struct
import zlib
from pathlib import Path

from PIL import Image, ImageDraw

from .codec import MAX_PIXELS, validate
from .model import PixelError

MAGIC = b"PXIMG\x08\r\n"
PREFIX = 20
MAX_META = 16_000_000
MAX_IMAGE = 32_000_000
BACKGROUND = (16, 20, 24, 255)
# Reversible category palette; GB still carry the complete 16-bit payload.
PALETTE = {
    16: (177, 220, 110),
    17: (192, 171, 230),
    18: (178, 220, 110),
    19: (230, 181, 127),
    20: (179, 220, 110),
    32: (80, 195, 222),
    48: (240, 175, 94),
    64: (222, 125, 112),
    80: (116, 135, 155),
    96: (154, 170, 220),
    112: (208, 190, 146),
    128: (92, 218, 170),
}
REVERSE = {v[0]: (k, v[1], v[2]) for k, v in PALETTE.items()}


def color_encode(rgba):
    if rgba[3] == 0:
        return BACKGROUND
    r, g, b, a = rgba
    if r not in PALETTE or a != 255:
        raise PixelError("image", "Unsupported source color in image")
    display, gm, bm = PALETTE[r]
    return (display, g ^ gm, b ^ bm, 255)


def color_decode(color):
    if tuple(color) == BACKGROUND:
        return None
    r, g, b, a = color
    if r not in REVERSE or a != 255:
        raise PixelError("image", "Unknown executable tile color")
    category, gm, bm = REVERSE[r]
    return [category, g ^ gm, b ^ bm, 255]


def pixel_bytes(im, count):
    if (count + 2) // 3 > im.width * im.height:
        raise PixelError("image", "Truncated pixel header")
    data = bytearray()
    for i in range((count + 2) // 3):
        color = im.getpixel((i % im.width, i // im.width))
        if color[3] != 255:
            raise PixelError("image", "Header pixels must be opaque")
        data.extend(color[:3])
    return bytes(data[:count])


def is_picture(im):
    if im.width * im.height < 3:
        return False
    rgba = im.convert("RGBA")
    try:
        return pixel_bytes(rgba, len(MAGIC)) == MAGIC
    except PixelError:
        return False


def encode_picture(doc, scale=8):
    if type(scale) is not int or not 4 <= scale <= 24:
        raise PixelError("image", "Tile scale must be 4..24")
    validate(doc)
    bundle = doc.get("bundle", {})
    if not isinstance(bundle, dict) or not isinstance(bundle.get("modules", {}), dict):
        raise PixelError("image", "Invalid project bundle")
    documents = {"main": doc, **bundle.get("modules", {})}
    if len(documents) > 128 or len(documents) != len(bundle.get("modules", {})) + 1:
        raise PixelError("image", "Invalid module count or reserved main module key")
    total = 0
    for mid, source in documents.items():
        if mid != "main" and (not mid.isdecimal() or not 0 <= int(mid) <= 65535):
            raise PixelError("image", "Invalid module ID")
        validate(source)
        total += math.prod(source["dimensions"])
    if total > MAX_PIXELS:
        raise PixelError("image", "Bundled source exceeds one million logical cells")
    margin = scale * 3
    width = max(
        192, max(d["dimensions"][0] * scale for d in documents.values()) + 2 * margin
    )
    header_rows = 2
    while True:
        y = header_rows + margin
        entries = []
        for mid, source in documents.items():
            spec = {
                k: copy.deepcopy(source[k])
                for k in (
                    "magic",
                    "versions",
                    "encoding",
                    "dimensions",
                    "spatial",
                    "metadata",
                )
                if k in source
            }
            entries.append({"id": mid, "origin": [margin, y], "source": spec})
            y += source["dimensions"][1] * scale + margin * 2
        height = y - margin
        metadata = {
            "format": "pixel-image",
            "version": "0.8",
            "scale": scale,
            "canvas": [width, height],
            "entry": bundle.get(
                "entry", doc.get("metadata", {}).get("filename", "main.pxl")
            ),
            "modules": entries,
        }
        raw = json.dumps(
            metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(raw) > MAX_META:
            raise PixelError("image", "Image annotations exceed 16MB")
        packed = zlib.compress(raw, 9)
        required = math.ceil((PREFIX + len(packed)) / (width * 3))
        if required <= header_rows:
            break
        header_rows = required + 1
    if width * height > MAX_IMAGE:
        raise PixelError(
            "image",
            "Rendered image exceeds 32 million pixels; use a smaller tile scale",
        )
    image = Image.new("RGBA", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for entry in entries:
        source = documents[entry["id"]]
        ox, oy = entry["origin"]
        for pixel in source["pixels"]:
            if pixel["rgba"][3] == 0:
                continue
            x, y = pixel["position"]
            left, top = ox + x * scale + 1, oy + y * scale + 1
            draw.rectangle(
                (left, top, left + scale - 3, top + scale - 3),
                fill=color_encode(pixel["rgba"]),
            )
    prefix = MAGIC + struct.pack(">III", len(packed), len(raw), zlib.crc32(packed))
    payload = prefix + packed
    payload += b"\x00" * ((-len(payload)) % 3)
    for i in range(0, len(payload), 3):
        image.putpixel(
            ((i // 3) % width, (i // 3) // width), tuple(payload[i : i + 3]) + (255,)
        )
    return image


def decode_picture(image):
    try:
        if image.width * image.height > MAX_IMAGE:
            raise PixelError("image", "Executable image exceeds pixel budget")
        image = image.convert("RGBA")
        prefix = pixel_bytes(image, PREFIX)
        if prefix[:8] != MAGIC:
            raise PixelError("image", "Unknown image version/signature")
        size, unpacked, crc = struct.unpack(">III", prefix[8:])
        if not 0 < size <= MAX_META or not 0 < unpacked <= MAX_META:
            raise PixelError("image", "Invalid header size")
        packed = pixel_bytes(image, PREFIX + size)[PREFIX:]
        if zlib.crc32(packed) != crc:
            raise PixelError("image", "Damaged pixel header (checksum mismatch)")
        decoder = zlib.decompressobj()
        raw = decoder.decompress(packed, MAX_META + 1)
        if len(raw) != unpacked or not decoder.eof or decoder.unused_data:
            raise PixelError("image", "Invalid or oversized compressed header")
        meta = json.loads(raw)
        if (
            not isinstance(meta, dict)
            or meta.get("format") != "pixel-image"
            or meta.get("version") not in ("0.8", "0.9")
            or meta.get("canvas") != [image.width, image.height]
        ):
            raise PixelError("image", "Unsupported image container")
        scale = meta.get("scale")
        entries = meta.get("modules")
        if (
            type(scale) is not int
            or not 4 <= scale <= 24
            or not isinstance(entries, list)
            or not 1 <= len(entries) <= 128
        ):
            raise PixelError("image", "Invalid tile scale or module table")
        documents = {}
        rects = []
        total = 0
        header_bottom = math.ceil((PREFIX + size) / (image.width * 3))
        for entry in entries:
            if not isinstance(entry, dict):
                raise PixelError("image", "Invalid module descriptor")
            mid = entry.get("id")
            source = copy.deepcopy(entry.get("source"))
            origin = entry.get("origin")
            if (
                not isinstance(mid, str)
                or mid in documents
                or (
                    mid != "main"
                    and (not mid.isdecimal() or not 0 <= int(mid) <= 65535)
                )
            ):
                raise PixelError("image", "Invalid or duplicate module ID")
            if not isinstance(source, dict) or "pixels" in source or "bundle" in source:
                raise PixelError(
                    "image", "Header cannot contain program pixels or nested bundles"
                )
            chunks = source.pop("linkChunks", None)
            if meta["version"] == "0.9":
                if not isinstance(chunks, list) or len(chunks) > 1954 or source.get("spatial") != {"links": []}:
                    raise PixelError("image", "Invalid or ambiguous picture link chunks")
                links = []
                for chunk in chunks:
                    if not isinstance(chunk, str) or not 2 <= len(chunk) <= 200000 or len(chunk) % 2 or any(c not in "0123456789abcdef" for c in chunk):
                        raise PixelError("image", "Invalid picture link chunk hex")
                    decoder = zlib.decompressobj()
                    raw_links = decoder.decompress(bytes.fromhex(chunk), 100001)
                    if len(raw_links) > 100000 or not decoder.eof or decoder.unused_data:
                        raise PixelError("image", "Invalid or oversized picture link chunk")
                    decoded_links = json.loads(raw_links)
                    if not isinstance(decoded_links, list) or not 1 <= len(decoded_links) <= 512 or len(links) + len(decoded_links) > 1000000:
                        raise PixelError("image", "Picture exceeds link limit")
                    links.extend(decoded_links)
                source["spatial"] = {"links": links}
            elif chunks is not None:
                raise PixelError("image", "Ambiguous picture links")
            source["pixels"] = []
            validate(source)
            if (
                not isinstance(origin, list)
                or len(origin) != 2
                or any(type(v) is not int for v in origin)
            ):
                raise PixelError("image", "Invalid module placement")
            x, y = origin
            w, h = source["dimensions"]
            total += w * h
            if (
                total > MAX_PIXELS
                or x < 0
                or y < header_bottom
                or x + w * scale > image.width
                or y + h * scale > image.height
            ):
                raise PixelError("image", "Module dimensions exceed image bounds")
            rect = (x, y, x + w * scale, y + h * scale)
            if any(
                rect[0] < r[2] and rect[2] > r[0] and rect[1] < r[3] and rect[3] > r[1]
                for r in rects
            ):
                raise PixelError("image", "Overlapping module bodies")
            rects.append(rect)
            for gy in range(h):
                for gx in range(w):
                    left, top = x + gx * scale + 1, y + gy * scale + 1
                    tile = image.crop((left, top, left + scale - 2, top + scale - 2))
                    if any(lo != hi for lo, hi in tile.getextrema()):
                        raise PixelError(
                            "image", f"Nonuniform tile at ({gx},{gy}) in module {mid}"
                        )
                    color = color_decode(tile.getpixel((0, 0)))
                    if color:
                        source["pixels"].append({"position": [gx, gy], "rgba": color})
            validate(source)
            documents[mid] = source
        if "main" not in documents:
            raise PixelError("image", "Missing entry module")
        main = documents.pop("main")
        main["bundle"] = {
            "version": "0.8",
            "entry": meta.get("entry", "main.pxl"),
            "modules": documents,
        }
        return main
    except PixelError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, zlib.error) as e:
        raise PixelError("image", f"Malformed executable image: {e}") from e


def save_picture(doc, path, scale=8):
    # No PNG text, EXIF or private chunks. All source and annotations are pixel data.
    encode_picture(doc, scale).save(Path(path), format="PNG", compress_level=9)
