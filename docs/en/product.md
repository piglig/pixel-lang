# PixelLang product overview

[中文](../zh-CN/product.md) · [English](product.md) · [Documentation](README.md)

PixelLang is a statically typed language whose colors and spatial relationships
express program semantics. Developers author algorithms and data-processing projects
in its own textual syntax, deliver programs as semantic-pixel PNGs, and develop,
debug and inspect execution in VS Code Studio.

## Audience and workflow

Primary uses include language implementation research, visual programming, strings,
arrays, sorting, statistics and multifile data processing. The workflow is:
author .pxl → check/test → export a PNG containing reachable modules → execute with
independent input → recover editable source. Recovery preserves meaning and names,
but not original comments or formatting.

## Product constraints

- Colors encode token categories/payloads, not VM opcodes directly. Coordinates, ordering, regions and connections contribute meaning.
- Images contain decodable pixel programs, use no OCR, and do not execute complete source, AST or bytecode hidden in metadata.
- Encoding, spatial AST, semantic checking, IR and VM are separate layers. Text can compile directly without an image roundtrip.
- Source, executable artifacts, runtime state and recordings are distinct. Replay never modifies source or repeats external writes.
- Builds use explicit dependencies, versioned formats and deterministic processing; complete artifacts can be compared under matching environments, inputs and options.
- The compiler is implemented in PixelLang. Python still hosts the VM, service, some editor utilities and bootstrap/reference implementation.

## Current capabilities

The language provides int64, finite float64, Unicode strings, arrays, string-keyed
maps, nominal records, generics, enums, Option, match, function values, closures,
structured errors and typed JSON. Modules use explicit exports and local dependency
locks. Libraries cover collection callbacks, sorting, CSV, text, statistics, JSON
and granted file I/O. The runtime checks bounds, types, overflow and budgets and
supports calls, recursion, GC, debugging and deterministic replay.

VS Code Studio provides navigation, diagnostics, formatting, build/test, PNG export/
recovery, breakpoints, variables and reverse stepping. Checked 2D editing supports
literal/operator replacement and adjacent statement movement. The time view's Z
axis represents execution transitions; source remains two-dimensional. The compiler
service supports cancellation, deadlines, revision guards and parse/IR/artifact caches.

## Current boundaries

There is no Go/Java syntax or ecosystem compatibility, full 3D source, network I/O,
network package registry, standalone machine-code backend or WASM backend. Tools
require Python; the Cython VM is an optional experiment. PNGs must retain exact pixels:
resizing, lossy compression and color transforms are invalid transport. File access
requires explicit grants; replay uses recorded effects. Running host calls cannot
always be interrupted immediately.

## Quality requirements

Text and pixel paths must agree, and diagnostics retain provenance. Formats and
caches cannot bypass type, resource, cancellation or dependency checks. Self-hosting
requires complete multigeneration artifact comparisons and generated-compiler
regressions. PNG delivery requires recovery of the complete compiler followed by
compilation of a fresh program. Run tests, performance and installation acceptance
separately; plans or previous results do not certify current code. See the
[language reference](reference/language/overview.md), [architecture](internals/architecture.md)
and [verification guide](contributing/verification.md).
