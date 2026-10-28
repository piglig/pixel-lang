# File types and recordings

[简体中文](../../../zh-CN/reference/formats/files.md) · [English](files.md) · [Documentation](../../README.md)

Source, compiled artifacts and execution recordings have distinct roles. A screenshot is not executable PNG, and a recording is not a substitute for editable source.

| Extension | Purpose |
| --- | --- |
| `.pxl` | UTF-8 text source |
| `.pixel` | Semantic-pixel JSON document |
| `.png` | Executable image container |
| `.pxb` | PixelVM bytecode |
| `.pixeltime` | Instruction-level recording |
| `.pxevents` / `.pxjournal` | Event recording / persistent application journal |

## Contract and interface

`.pixel` is UTF-8 JSON with magic PIXELLANG, versions
`{language:"0.9",encoding:"0.9",format:"0.9"}`, encoding rgba8, positive 2D
dimensions, a sparse pixels list of position/RGBA objects, spatial.links and optional
metadata. Positions are unique, nonnegative and in bounds; at most one million
logical cells and 64 MB for a native source file. Links carry next/child endpoints and region self-markers.

Optional bundle version 0.9 contains the entry filename and numbered modules.
Metadata may preserve symbol names and text ranges, but not executable values,
mutability or entry rules. The entry keyword is in the pixels. A bare three-token
expression is not an executable module: wrap it in an entry function; generated
addition.pixel demonstrates the current native structure.

Raw PNG uses exact RGBA with transparent empty cells. Optional pixellang ancillary
metadata carries explicit links; removing it can change raw-image relationships.
Delivery images instead use the physical [executable container](png.md),
which carries all module/relationship information in image pixels and survives
ancillary metadata removal. Lossy edits/rescaling are not valid transport.

`.pxb` is JSON with magic PIXELVM, language/VM versions 0.9 and informational compiler
version 0.9.0. `.pixeltime` is a separate version-1.0 recording containing the text source project,
input, execution length/cursor, quotas, captured file effects, cancellation position
and a verified source snapshot. Generated pixel projections are omitted and are rebuilt on demand. Recordings never mutate source and replay
never repeats external writes. Readers validate their supported versions; PNG container compatibility is described in the image contract.

Native (`selfhost`) recordings require a source snapshot containing `files` and
`entry`. A timeline owns a detached copy of the compiler's snapshot; its bytecode
digest identifies the immutable code actually executed. Restore validates the
snapshot before compiling and compares the resulting program with the recorded
digest. Changing caller-owned compilation data cannot rewrite an existing session's
source snapshot or executable identity.

Instruction timelines require a native compiled debug artifact. Restoration accepts
only recordings with the `selfhost` backend and recompiles their source snapshot
with the native compiler. There is no Python frontend fallback or legacy recording
conversion path.

## Further reading

- [Documentation index](../../README.md)
