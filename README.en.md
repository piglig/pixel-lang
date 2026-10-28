<p align="center">
  <img src="assets/branding/icon.png" alt="PixelLang" width="128" height="128">
</p>

<h1 align="center">PixelLang</h1>

<p align="center"><strong>Compute with space. Deliver programs as images.</strong></p>

<p align="center">
  <a href="docs/en/product.md"><img src="https://img.shields.io/badge/status-development_preview-2563eb" alt="Development preview"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-64748b" alt="Apache-2.0"></a>
</p>

<p align="center"><a href="README.md">简体中文</a> · <a href="README.en.md">English</a></p>

PixelLang is a statically typed spatial programming language. Write algorithms and state transitions as text, compute with 3D entities, coordinates and connections, then deliver the program as a PNG with recoverable source.

**[Install](docs/en/guides/installation.md)** · **[Get started](docs/en/learn/getting-started.md)** · **[Documentation](docs/en/README.md)** · **[VS Code Studio](docs/en/guides/studio.md)**

> Development preview: syntax and formats may change incompatibly. Executing a PNG still requires PixelLang; the image is neither a screenshot nor a standalone machine-code program.

## A small program

```pixellang
import "std/collections.pxl" as collections

effect fn main() {
    let grouped = collections.GroupBy([1, 2, 3, 4], fn(value: int) = value % 2)
    print(grouped[1])
}
```

This prints `[1, 3]`. Arrays, maps and records use value semantics; generic inference keeps common calls concise.

## Why PixelLang

| Capability | Meaning |
| --- | --- |
| Computation with space | Frames, 3D neighborhoods, directions and typed links are program semantics |
| Explicit state transitions | Flows read old state and commit the next state atomically on success |
| Image delivery and recovery | PNG carries the entry and reachable modules, recoverable as multi-file source |
| Observable execution | Studio provides breakpoints, variables, spatial inspection and history replay |
| Self-hosted compiler | The compiler is written in PixelLang; ordinary builds use its native executable |

## Run and deliver

Follow [installation](docs/en/guides/installation.md), then save the program as `main.pxl`:

```sh
pixelrun main.pxl
pixelpack main.pxl -o program.png
pixelrun program.png
pixelunpack program.png -o recovered
```

The recovery directory must be empty. Recovery preserves meaning, not original comments or formatting. Preserve exact pixels in transit. An initial source installation requires a platform-native compiler, not just Python dependencies.

## Develop in VS Code

Install a platform-matched VSIX, select your configured Python interpreter, and run **PixelLang: Open Studio**. Use F5 for source debugging and **Open Native Graph** to inspect dependencies and spatial values.

See the [Studio guide](docs/en/guides/studio.md) for installation, configuration, export and debugging.

## Examples

| Project | Learn about |
| --- | --- |
| [Order state machine](examples/native-orders/README.en.md) | Typed events, business rejection and state transitions |
| [3D neighborhoods](examples/native-space/README.en.md) | How changing Z changes program output |
| [Weighted routes](examples/weighted-routes/README.en.md) | Link payloads and stepwise propagation |
| [Spatial elevator](examples/native-elevator/README.en.md) | Multi-file controllers, snapshots and independent instances |
| [Ledger](examples/ledger/README.en.md) | CSV validation, grouping, sorting and reports |
| [Log analysis](examples/log-analysis/README.en.md) | Streaming file reads and typed aggregation |

## Scope

The toolchain targets POSIX hosts such as macOS and Linux and needs Python 3.11+ with a matching native compiler. Python PixelVM provides ordinary application execution and debugging; native code generation serves the compiler and verification. Dependencies are local projects, file access is explicitly granted, and there is no network package registry or language-level network I/O.

See [project scope](docs/en/product.md) and [architecture](docs/en/internals/architecture.md).

## Contributing

Reproducible bug reports, documentation improvements and code changes are welcome. Start with [contributing](docs/en/contributing/README.md). Maintain both documentation languages and choose checks appropriate to the change.

## License

[Apache License 2.0](LICENSE).
