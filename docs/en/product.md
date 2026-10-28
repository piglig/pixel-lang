# What is PixelLang?

[简体中文](../zh-CN/product.md) · [English](product.md) · [Documentation](README.md)

PixelLang is a statically typed spatial programming language. Write programs as text, deliver and recover them through semantic-pixel PNGs, and use VS Code Studio to edit, debug and inspect state.

## Capabilities

| Capability | Applications |
| --- | --- |
| Values, generics, enums and closures | Algorithms, multi-file programs and typed data processing |
| Native spatial types | 3D neighborhoods, coordinate transforms, entities and weighted connections |
| Atomic flow transitions | Order state, controllers and stepwise propagation |
| Executable PNG | Carry the entry and reachable modules; recover an editable project |
| Debugging and replay | Inspect provenance, variables, historical state and recorded file effects |

## Program delivery

Write `.pxl` → check and test → export PNG → execute with separate input → recover source.
Exact colors, regions and connections carry program structure. PNG execution uses neither OCR nor executable source or bytecode hidden in metadata. Text compiles directly without an image round-trip. Recovery preserves meaning, not original comments or formatting.

## Space and time

XYZ are spatial coordinates that participate in computation. Logical time T is explicit input or execution history; cameras, zoom and graph layout are views. `fn` expresses computation, `flow` owns persistent state and atomic commits, and hosts or libraries schedule events. Spatial programs need neither 3D model files for delivery nor spatial types in every algorithm.

## Current boundaries

- This is a development preview; syntax, protocols and package formats may change incompatibly.
- The compiler is written in PixelLang. CLI and Studio use its native executable; Python provides application execution, services and debug replay.
- The toolchain needs a platform-matched compiler bundle. [Installation](guides/installation.md) distinguishes existing wheels from initial source preparation.
- Local dependencies are explicit and locked. There is no network package registry or language-level network I/O.
- Preserve exact PNG pixels. Program input, authorized data directories and the runtime remain separate.
- Native code generation serves the compiler and verification; normal application development and debugging use PixelVM.

## Where to start

Read [your first program](learn/getting-started.md), then explore [spatial programming](guides/spatial-programming.md) and [state machines](guides/state-machines.md). Contributors can start with [architecture](internals/architecture.md) and [contributing](contributing/README.md).
