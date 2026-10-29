# Command-line tools

[简体中文](../../zh-CN/guides/cli.md) · [English](cli.md) · [Documentation](../README.md)

Use the command line to run a program, build an image or recover code from a PNG. Find the task you need below; add `--help` to any command to see its options.

## Choose a command

| Command | Purpose |
| --- | --- |
| pixel init [directory] | Create a runnable project with a test; destination must be empty |
| pixel doctor [--json] | Check Python, host support, Pillow PNG codec and compiler checksum |
| pixel lock/test/build/run | Manifest dependencies, tests, builds and execution |
| pixelc | Compile source or images |
| pixelrun | Run source, images or bytecode |
| pixelinspect | Inspect native source, debug, graph, ir or bytecode |
| pixelfmt | Format text |
| pixelrender / pixelpack | Generate images |
| pixelunpack | Recover a project from a delivery PNG |
| pixeldebug | Debug tooling entry |
| pixelcompiler | JSON-lines compiler service |

Use --help for each command's options. Runtime input is a separate string/file;
data-file access requires explicit read/write roots. Errors go to stderr with a
nonzero exit status; supported --json options provide structured diagnostics.
See [projects](projects.md) for pixel.toml, dependency locks and tests. VS Code is
the only Studio host: there is no pixelstudio command or browser server.
See the [workstation guide](studio.md).

## Inspect compilation

`pixelinspect main.pxl --stage ir` uses the bundled PixelLang compiler, without a
Python frontend. Source supports `source` (semantic pixel document), `debug`
(debug mappings), `graph` (native spatial/dataflow projection), `ir` and `bytecode`.
The default `all` returns source, debug, ir and bytecode. PNG, `.pixel`/`.json` semantic documents and `.pxb` inputs
support `all` or `bytecode`; all returns an object with a bytecode field. Other
stages require `.pxl` source. Bytecode is validated before inspection.
Use `-o` to write JSON; failed inspection preserves an existing output file.
The old spatial_ast/canonical_ast stages have been removed.

Compiling, running and debugging a semantic pixel document also use the native
compiler's pixel decoder and type checker. Multi-module documents carry their
dependencies in the bundle; these commands do not fall back to the Python frontend.

`pack`, `render` and `unpack` use the native compiler for pixel documents and PNGs.
Formatting a pixel document regenerates its canonical semantic layout; this differs
from formatting `.pxl` text, which preserves comments and literal spellings.
Use `unpack -o DIRECTORY` to restore multiple modules. Formatting a multi-module
document to one `.pxl` file is rejected instead of dropping its dependencies.

## Format source

`pixelfmt main.pxl -o formatted.pxl` uses the native compiler's syntax formatter.
It preserves existing line breaks, comments and literal spellings, normalizes
horizontal spacing and uses four-space delimiter indentation. It parses before
and after formatting and verifies unchanged syntax structure. Imports need not
be available; formatting does not type-check the project. The output may be the
input file. Syntax errors preserve the existing output file.

## Further reading

- [Documentation index](../README.md)
- [Install the toolchain](installation.md)
