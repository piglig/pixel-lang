# Use VS Code Studio

[简体中文](../../zh-CN/guides/studio.md) · [English](studio.md) · [Documentation](../README.md)

Write, run and debug PixelLang without leaving VS Code. Studio lets you inspect values, explore spatial relationships and revisit earlier execution steps. It runs locally as a VS Code extension.

## Install and configure

1. Complete [toolchain installation](installation.md) and check `pixel doctor`.
2. Run **Extensions: Install from VSIX** and choose a platform-matched package.
3. Open your project and run **PixelLang: Select Python Interpreter**.
4. Run **PixelLang: Check Environment** and inspect the PixelLang output channel.

The extension bundles its compiler and host code; the interpreter still needs Python 3.11+ and Pillow 10–12. Set `pixellang.pythonPath` if needed. Projects select an entry through `pixel.toml`; standalone files can use `pixellang.entry`.

To build the extension from source, prepare the local compiler first, then run from the repository root:

```sh
npm --prefix vscode ci
npm --prefix vscode run package
```

Packaging does not publish the extension. See [verification](../contributing/verification.md) for platform bundles and installed checks.

## Write programs

Open a `.pxl` file and start typing. Studio suggests completions, highlights errors and shows a value’s type when you hover over it. You can jump to a definition, find references, rename a symbol or format the file using familiar VS Code commands.

Formatting works on unsaved code, even if an imported file is missing. Rename checks the project before applying changes; if the new name would conflict with another name or leave errors, Studio asks you to resolve that first.

See [projects](projects.md) for save-and-test, PNG builds, dependency locks and individual tests.

## Build, run and export

Run **PixelLang: Open Studio**:

| Action | Result |
| --- | --- |
| Build | Compile the current buffer snapshot into debugging artifacts |
| Run / Step | Execute with runtime input or advance incrementally |
| Program image | Generate an executable image on demand |
| Export PNG | Choose a destination, then generate and save PNG |
| Compiler · IR | Request intermediate representation on first open |
| Recover Project from PNG | Restore source into an empty directory |

Ordinary builds, stepping and recording recovery do not automatically generate PNG. Source or input changes invalidate the old run. Build notifications show collection and waiting time rather than a completion percentage; both phases support cancellation. The output channel records collection, queueing, compilation and debug initialization costs.

## Debug source and history

Press F5 for PixelLang debugging: breakpoints, step in/over/out, pause, reverse instruction, call stacks and paginated variables. Time changes invalidate old variable references. Missing or changed files display readonly source matching the executed artifact.

In launch configuration, `program` selects the entry, `input` is the text read by `input()`, and `args` is the program-argument array. File access requires `pixellang.readRoot` / `pixellang.writeRoot`. Pause can resume; Cancel ends the run while retaining history. Cancellation takes effect between execution batches and cannot forcibly interrupt an active host call.

**Save timeline** writes `.pixeltime`; **PixelLang: Open Time Recording** opens it again. Recovery recompiles the recorded source snapshot and checks the bytecode digest. It does not depend on current project files or repeat file writes. See [file types](../reference/formats/files.md).

## Space and dependency graphs

Run **PixelLang: Open Native Graph** to view named computations, state, child instances, `next` and outputs. Expression edits update the same unsaved source and support editor Undo. Add state accepts a name, type and initializer. Fix incomplete syntax in the text editor first.

While paused, **Paused execution** shows flow inputs, computed results, old state and staged values. The **Space** entry on Space/Entity values paginates entities, frames, snapshots and neighborhoods; complex payloads expand on demand. Resuming, editing source or switching sessions clears stale values.

- **Orbit**, zoom and graph layout change the view only.
- **Source coordinates** edits coordinates with direct literal provenance.
- **Move X/Y/Z** drags along one axis; release commits one undoable source edit and Escape cancels.
- **Reveal expression** navigates to computed positions without replacing expressions with constants.

A coordinate edit affects every execution of that source expression; restart debugging to observe its result. Spatial Z is a coordinate. Display Z in **3D · Time** is a logical transition index; these are different views.

## Two-dimensional semantic editing

**2D · Edit** connects semantic pixels to source. Change literals or operators, or move adjacent statements within one statement list. The compiler validates the multi-file snapshot before the extension applies one WorkspaceEdit. Stale or invalid proposals do not write source. Arbitrary painting and full 3D source sculpting are not provided.

## Event applications

Use **PixelLang: Open Native Application** with source or PNG to enter typed events, advance steps, inspect history and save application journals. **Debug event** enters source debugging for the selected event. See [state machines](state-machines.md) for scheduling and recording contracts.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Missing Python or Pillow | Select Python Interpreter → Check Environment |
| Slow build | Output channel: separate collection, queueing, compilation and image generation |
| Coordinate cannot be dragged | Direct literal provenance and the viewing angle relative to the selected axis |
| Old state after an edit | Restart debugging; old snapshots do not turn into new runs |
| File access rejected | Read/write roots and relative paths |

Build and background analysis use separate processes. See the [compiler service](../reference/compiler-service.md) for incrementality and budgets.
