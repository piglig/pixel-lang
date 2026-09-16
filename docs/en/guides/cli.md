# Command-line tools

[中文](../../zh-CN/guides/cli.md) · [English](cli.md) · [Documentation](../README.md)

For repository development use uv sync --locked, or install a built wheel with pip.
Commands have corresponding python -m pixellang entry points.

| Command | Purpose |
| --- | --- |
| pixel init [directory] | Create a runnable project with a test; destination must be empty |
| pixel doctor [--json] | Check Python, host support, Pillow PNG codec and compiler checksum |
| pixel lock/test/build/run | Manifest dependencies, tests, builds and execution |
| pixelc | Compile source or images |
| pixelrun | Run source, images or bytecode |
| pixelinspect | Inspect spatial_ast, canonical_ast, ir or bytecode |
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
