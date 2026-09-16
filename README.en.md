<p align="center">
  <img src="assets/branding/icon.png" alt="PixelLang spatial icon" width="160" height="160">
</p>

<h1 align="center">PixelLang</h1>

<p align="center"><strong>Write code. Deliver an image.</strong></p>
<p align="center">Static types · Executable semantic pixels · A VS Code workstation</p>

<p align="center">
  <a href="docs/en/product.md"><img src="https://img.shields.io/badge/status-Development%20Preview-2563eb?style=flat" alt="Development Preview" height="20"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/Python-%E2%89%A5%203.11-3776ab?style=flat&amp;logo=python&amp;logoColor=white" alt="Python ≥ 3.11" height="20"></a>
</p>

<p align="center">
  <a href="README.md">简体中文</a> · <strong>English</strong>
</p>

---

PixelLang lets you author programs as text, deliver them as a PNG, and recover editable multifile projects from the image. Colors, coordinates and connections express program semantics. VS Code Studio connects source code, pixels and execution.

**[Quick start](#quick-start)** · **[Documentation](docs/en/README.md)** · **[Use Studio](docs/en/guides/studio.md)** · **[Contribute](docs/en/contributing/README.md)**

```go
import "std/sort.pxl" as sort

fn main() {
    let values = sort.Ints([9, 2, 7])
    for value in values {
        print(value)
    }
}
```

> **Development preview** · The toolchain requires Python 3.11+. Images contain the program and its reachable modules; execution still requires the PixelLang runtime. Source recovery does not preserve original comments or formatting.

## Why PixelLang

| Capability | What you can do |
| --- | --- |
| **Images as programs** | Export a semantic-pixel PNG, share the program, and recover editable source. |
| **Explicit types** | Build multifile programs with generics, records, enums, `Option`, closures and structured errors. |
| **Algorithms and data processing** | Work with Unicode text, arrays, maps, CSV and JSON; sort, group and compute statistics. |
| **Inspectable execution** | Set breakpoints, inspect variables, step backward and explore execution along a timeline in Studio. |

Image loading decodes pixels directly, without OCR or execution of complete source text or bytecode hidden in metadata. Text can compile directly to bytecode without first exporting an image.

## Quick start

Install **Python 3.11+** and **[uv](https://docs.astral.sh/uv/getting-started/installation/)**, then get the project:

```sh
git clone https://github.com/piglig/pixel-lang.git
cd pixel-lang
uv sync --locked
```

Save the example above as `main.pxl` and run it:

```sh
uv run pixelrun main.pxl
```

Output:

```text
2
7
9
```

### Deliver and recover

```sh
# Package source as an executable image
uv run pixelpack main.pxl -o program.png

# Run the program directly from the image
uv run pixelrun program.png

# Recover an editable project into an empty directory
uv run pixelunpack program.png -o recovered
```

The PNG contains all reachable modules and runs without the original `.pxl` files. Preserve exact pixels: avoid resizing, lossy compression and color transforms. Learn more: [projects and dependencies](docs/en/guides/projects.md) · [executable PNG format](docs/en/reference/formats/png.md).

## Develop in VS Code

After installing the extension, run **PixelLang: Open Studio**, or open a `.pxl` file and press **F5** to debug.

- **Author**: live diagnostics, completion, definitions and references, rename and formatting.
- **Build**: project tests, PNG export and recovery, on-demand compiler IR inspection.
- **Debug**: breakpoints, call stacks, paged variables, reverse stepping and recordings.
- **Explore**: checked 2D semantic editing and a 3D view with execution time on the Z axis.

Building the extension requires Node.js and npm. From the repository root:

```sh
npm --prefix vscode ci
npm --prefix vscode run package
```

In VS Code, choose **Extensions → Install from VSIX** and select the generated package. The Python environment needs Pillow; `uv sync --locked` above installs the runtime dependencies.

[Studio installation, configuration and usage →](docs/en/guides/studio.md)

## Example projects

| Example | Covers | Run tests |
| --- | --- | --- |
| [Data report](examples/data-report/main.pxl) | CSV, sorting, statistics | `uv run pixel test examples/data-report` |
| [Callback analysis](examples/callback-analysis/main.pxl) | Closures, filtering, grouping, stable sorting | `uv run pixel test examples/callback-analysis` |
| [Ledger](examples/ledger/README.en.md) | File I/O, validation, grouped reports | `uv run pixel test examples/ledger` |
| [Log analysis](examples/log-analysis/README.en.md) | Multifile JSON Lines, time filtering, aggregation | `uv run pixel test examples/log-analysis` |

## Documentation

| To learn about | Start here |
| --- | --- |
| Product scope and boundaries | [Product overview](docs/en/product.md) |
| Syntax and data semantics | [Language reference](docs/en/reference/language/overview.md) · [Standard library](docs/en/reference/standard-library.md) |
| Projects, dependencies and tests | [Project guide](docs/en/guides/projects.md) · [Command-line tools](docs/en/guides/cli.md) |
| Compiler implementation | [Architecture](docs/en/internals/architecture.md) · [Self-hosting](docs/en/internals/self-hosting.md) · [Python responsibilities](docs/en/internals/python.md) |
| All documentation | [简体中文](docs/zh-CN/README.md) · [English](docs/en/README.md) |

The compiler is written in PixelLang and currently runs on the Python implementation of PixelVM. Source uses a 2D spatial model; Studio's displayed Z axis represents time. Full 3D source, network I/O and machine-code/WASM backends are not supported.

## Contributing

Reproducible bug reports, documentation improvements and code contributions are welcome. Read the [contribution guide](docs/en/contributing/README.md) and choose verification appropriate to your change.

```sh
# Check bilingual documentation, navigation and links
uv run python scripts/check_docs.py

# Run explicitly selected tests with a fresh external output directory
uv run python scripts/verify_preview.py related \
  --test tests.test_compiler_service --output /tmp/pixel-related-NEW
```

Merge checks, full bootstrap, image roundtrips and real-editor acceptance run separately; see [layered verification](docs/en/contributing/verification.md). Documentation is maintained in both Chinese and English.
