# Source, executable and recording formats

[中文](../../../zh-CN/reference/formats/files.md) · [English](files.md) · [Documentation](../../README.md)

`.pixel` is UTF-8 JSON with magic PIXELLANG, versions
`{language:"0.8",encoding:"0.8",format:"0.8"}`, encoding rgba8, positive 2D
dimensions, a sparse pixels list of position/RGBA objects, spatial.links and optional
metadata. Positions are unique, nonnegative and in bounds; at most one million
logical cells and 64 MB for a native source file. Links carry next/child endpoints and region self-markers.

Optional bundle version 0.8 contains the entry filename and numbered modules.
Metadata may preserve symbol names and text ranges, but not executable values,
mutability or entry rules. The entry keyword is in the pixels. A bare three-token
expression is not an executable module: wrap it in an entry function; generated
addition.pixel demonstrates the current native structure.

Raw PNG uses exact RGBA with transparent empty cells. Optional pixellang ancillary
metadata carries explicit links; removing it can change raw-image relationships.
Delivery images instead use the physical [executable container](png.md),
which carries all module/relationship information in image pixels and survives
ancillary metadata removal. Lossy edits/rescaling are not valid transport.

`.pxb` is JSON with magic PIXELVM, language/VM versions 0.8 and informational compiler
version 0.8.0. `.pixeltime` is a separate version-0.8 recording containing source,
input, execution length/cursor, quotas, captured file effects, cancellation position
and optional verified source snapshot. Recordings never mutate source and replay
never repeats external writes. Readers validate their supported versions; PNG container compatibility is described in the image contract.
