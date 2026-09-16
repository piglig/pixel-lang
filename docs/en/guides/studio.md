# VS Code Studio workstation

[中文](../../zh-CN/guides/studio.md) · [English](studio.md) · [Documentation](../README.md)

Studio is a VS Code extension without an HTTP server or external browser. It bundles
the compiler; a Python stdio service owns compilation, execution, images and recordings.

## Installation and authoring

Run uv sync --locked at the repository root, then install a local VSIX through
Extensions → Install from VSIX. Python 3.11+ and Pillow are required. The extension
prefers the workspace .venv, then python3 (python on Windows); configure pixellang.pythonPath
if needed. Open .pxl files for live diagnostics, completion, definitions/references,
rename and comment-preserving indentation formatting. Projects use pixel.toml;
standalone workflows can set pixellang.entry to a fixed entry.

## Build and run

Run **PixelLang: Open Studio**. Build compiles the current buffer snapshot into a PNG
containing reachable modules; Run executes with supplied input and Step advances one
transition. Source/input changes invalidate the old run. Program image displays the
executable PNG, Export PNG saves it, and **PixelLang: Recover Project from PNG** restores
source into an empty directory without comments or original formatting. Compiler · IR
requests IR on first opening; ordinary stepping/replay does not regenerate it.
See [projects](projects.md) for save/test/build and lock commands.

## Spatial editing

In 2D · Edit, select a module, pixel or Selected semantics item to inspect meaning and
reveal source. Text selection highlights the smallest matching target. Set literal and
Set operator change literals/binary operators; Move ↑/↓ moves adjacent statements in
the same list. The service checks the exact snapshot through full multifile pixel
compilation; the extension rechecks buffers and applies one WorkspaceEdit, reversible
with ordinary Undo. Invalid/stale proposals never write source or execute file effects.
Refresh after other edits; recording snapshots stay bound to their original execution.
Arbitrary painting and full 3D source editing are unavailable. Canonical paths identify
files while edits reuse open document URIs, preventing duplicate symlink buffers.

## Time and debugging

In 3D · Time, X/Y are module-local source positions and Z is logical transition number.
Drag to orbit, scroll to zoom, select a voxel or use the time slider. A bounded window
surrounds the selected time and keeps modules separate. State comes from checkpoints
and deterministic replay, never interpolation. Save timeline writes .pixeltime;
**PixelLang: Open Time Recording** reopens it without the original project. Format 0.8
stores source, input, budgets, checkpoint interval and cancellation position.

Press F5 and choose PixelLang for breakpoints, step in/over/out, pause, backward
instruction stepping, call stacks, named locals, heap and operand stack. Termination
retains a stopped state for reverse inspection; Stop closes the session. Values page
lazily and references expire when time changes. Missing/changed source opens as readonly
snapshots matching the artifact, including standard libraries. Launch supports program,
input and stopOnEntry; input is the string consumed by input(). File access requires
pixellang.readRoot/writeRoot grants; network access is unavailable. Pause stops automatic
advancement and can resume. Cancel ends execution but retains history, taking effect
after the current batch of at most 2,000 instructions, without interrupting a host call.

## Build the extension

From the repository root:

```sh
npm --prefix vscode ci
npm --prefix vscode run package
npm --prefix vscode test
```

The output is pixellang-studio-0.9.0.vsix; packaging does not publish. Tests use pinned
VS Code 1.137.0, or PIXELLANG_VSCODE for an explicit local executable. Real interaction
tests require an unlocked desktop; see [contributing](../contributing/README.md).

## New projects and environment checks

The command palette provides `PixelLang: Create Project`, `PixelLang: Check Environment` and `PixelLang: Select Python Interpreter`. Creation adds a project under the selected parent and opens a new window. Environment checks write details and fixes to the PixelLang output panel. Select Python 3.11+ with Pillow 10–12 installed.
