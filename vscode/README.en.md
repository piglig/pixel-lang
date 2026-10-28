# PixelLang Studio

[简体中文](README.md) · [English](README.en.md) · [Full guide](../docs/en/guides/studio.md)

Write, run and debug PixelLang in VS Code, with spatial inspection and execution history.

## Features

- Diagnostics, completion, formatting, definitions, references and rename.
- Project tests, builds, executable PNG export and source recovery.
- Breakpoints, call stacks, paginated variables, reverse stepping and replay.
- Flow dependency graphs, entities, links and undoable source-coordinate edits.

## Installation

Requires VS Code 1.90+, Python 3.11+, Pillow 10–12 and a platform-matched VSIX.
Install with **Extensions: Install from VSIX**, then run **PixelLang: Select Python Interpreter** and **PixelLang: Check Environment**.

## Get started

Open a `.pxl` file, run **PixelLang: Open Studio**, or press F5 to debug.
Use **PixelLang: Open Native Graph** for spatial programs and **PixelLang: Open Native Application** for event applications.
File access requires `pixellang.readRoot` and `pixellang.writeRoot`. Select project entries through `pixel.toml` or `pixellang.entry`.

## Build from source

Prepare the native compiler using [installation](../docs/en/guides/installation.md), then run from the repository root:

```sh
npm --prefix vscode ci
npm --prefix vscode run package
```

Packaging does not publish the extension. This is a development preview without legacy protocol compatibility. Licensed under [Apache-2.0](../LICENSE).
