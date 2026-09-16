# Executable image container 0.9 (language/encoding 0.8)

[中文](../../../zh-CN/reference/formats/png.md) · [English](png.md) · [Documentation](../../README.md)

The image is an RGBA PNG with no rendered text. Decoding requires exact pixels. PNG ancillary chunks are neither required nor used to carry source text, AST, bytecode or dependencies. The visible body contains the actual spatial token grids for the entry and all reachable modules. The image is not a screenshot of code.

## Physical container

The first RGB bytes encode `PXIMG\x08\r\n`, followed by three big-endian uint32 values: compressed header length, expanded header length and CRC32 of the compressed payload. Together these occupy 20 bytes. Subsequent RGB bytes contain zlib-compressed JSON describing format/version, scale, canvas dimensions, entry and module placements. All header pixels are opaque. The metadata is itself physical pixel data, not a PNG text chunk.

The header contains each module's source schema excluding pixels and recursive bundles: versions, dimensions, explicit connections and optional annotations (file names, symbol names, import names, text ranges). No complete textual program or compiled executable is embedded. Symbol annotations enable readable recovery; IDs and body tokens govern execution. Structural regions and connections retain the same role as in the native spatial container.

Module grids have explicit non-overlapping origins and a margin of three tiles. The PixelLang encoder compares deterministic shelf packing with vertical stacking and chooses the smaller canvas; the decoder uses the recorded origins, not an assumed arrangement. Each semantic tile has a uniform `scale - 2` square interior. Tile scale is an integer from 4 to 24. Category R bytes map reversibly to a brighter palette; G/B payload bytes are XOR-mapped using fixed category masks in `pixellang/picture.py`. Empty cells use `(16,20,24,255)`. Category palette and logical coordinates recover the exact input grid.

The decoder validates dimensions, header size/CRC, placements, non-overlap, uniform interiors, token colors and source schema. Limits: 32 million physical pixels, 1 million total logical cells, 16 MB header, 128 modules. CRC detects header corruption; it is not an authenticity signature. A uniformly changed valid tile is a changed program and may execute with different meaning.

Do not resize, apply color correction, screenshot or convert to JPEG. Byte-identical RGBA data saved in a fresh PNG without ancillary metadata continues to execute. There are no external project files required for bundled imports. Input data is independently supplied at runtime.

## Language encoding extensions

Source language/encoding/format and VM/IR are 0.8. Old artifacts are rejected. Literal and builtin categories:

- R=18: int64 head, payload zero, followed by four R=112 uint16 words.
- R=19: UTF-8 string head, payload byte length, followed by R=112 words; final odd-byte padding is zero.
- R=112: literal continuation word (G/B big endian).
- R=128: deterministic builtin index from `typesys.BUILTINS`.

Type and punctuation tables add strings, unit and typed-array delimiters. `do` denotes a discarded expression. Typed arrays lower to explicit typed spatial literals; index access/assignment, builtin calls and unit returns lower to versioned AST/IR/VM operations (`ARRAY`, `INDEX`, `INDEX_SET`, `BUILTIN`, `CONCAT`). The VM provides typed heap references and checked operations. Text-to-image conversion therefore extends the language itself rather than adding a separate execution engine.

## Round trip

`pixelpack main.pxl -o program.png` resolves the source project and produces the image. `pixelrun program.png` reconstructs spatial modules and compiles through the normal pipeline. `pixelunpack program.png -o directory` parses spatial AST and pretty-prints canonical named `.pxl` files. Comments and original formatting cannot be recovered. An executable entry marker recovers the parameterless main declaration; no top-level call is injected.

## Bounded connection headers (container 0.9)

The native writer emits header version `0.9`, preserving the `PXIMG\x08\r\n` prefix and the language/semantic-pixel format `0.8`. Each module stores an empty `spatial.links` and a `linkChunks` array. Each chunk is a lowercase hexadecimal string containing zlib-compressed UTF-8 JSON for at most 512 connections. Connections keep their original order and schema; neither source text nor bytecode is introduced.

Limits per module: at most 1,000,000 links and 1,954 chunks. A chunk expands to at most 100,000 bytes and has at most 200,000 hex characters. Empty chunks, malformed hex/zlib/JSON, trailing compressed data, and simultaneous inline and chunked links are rejected. Empty link tables use an empty chunk array. General JSON node/string limits remain unchanged. Chunks are prepared once and reused while planning header placement.

Readers also accept legacy container `0.8` with inline links; a `0.9` header must supply chunks. This does not change semantic source documents or bundled language versions.
