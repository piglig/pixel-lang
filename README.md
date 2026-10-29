<p align="center">
  <img src="assets/branding/icon.png" alt="PixelLang" width="128" height="128">
</p>

<h1 align="center">PixelLang</h1>

<p align="center"><strong>Write code. Ship pixels.</strong></p>

<p align="center">
  <a href="docs/en/product.md"><img src="https://img.shields.io/badge/status-development_preview-2563eb" alt="Development preview"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-64748b" alt="Apache-2.0"></a>
</p>

<p align="center"><a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a></p>

PixelLang is a spatial programming language. Build programs that work with positions, connections and changing state, then share them as PNG images that others can run and turn back into editable code.

**[Install](docs/en/guides/installation.md)** · **[Get started](docs/en/learn/getting-started.md)** · **[Documentation](docs/en/README.md)** · **[VS Code Studio](docs/en/guides/studio.md)**

> PixelLang is in development preview. You can try it today, but syntax and file formats are still evolving. You’ll need the PixelLang runtime to run a program shared as a PNG.

## A small program

```pixellang
import "std/collections.pxl" as collections

effect fn main() {
    let grouped = collections.GroupBy([1, 2, 3, 4], fn(value: int) = value % 2)
    print(grouped[1])
}
```

This groups the numbers by their remainder when divided by two, then prints the odd numbers: `[1, 3]`. You can start with familiar tasks like sorting, grouping and reading files, and explore spatial features when your program needs them.

## What can you build?

- **Programs that understand space.** Find nearby entities, move them in three dimensions, or give their connections a cost or a status.
- **Programs that respond to events.** Model an order moving from one state to another, or a controller reacting to input. A `flow` keeps state between steps.
- **Programs you can share as images.** Export a PNG, run it with new input, or recover its source to keep working on it.

Use VS Code Studio to set breakpoints, inspect values and explore how your program changes over time.

## Run and deliver

Follow [installation](docs/en/guides/installation.md), then save the program as `main.pxl`:

```sh
pixelrun main.pxl
pixelpack main.pxl -o program.png
pixelrun program.png
pixelunpack program.png -o recovered
```

You now have an image you can share and a recovered project you can edit. Choose an empty folder for `recovered`, and share the original PNG without resizing or editing it. Recovered code behaves the same, though comments and formatting are not preserved.

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

## Before you start

The toolchain currently supports macOS and Linux and requires Python 3.11+ and a matching native compiler bundle. The [installation guide](docs/en/guides/installation.md) walks you through setting it up.

PixelLang is still growing. Packages are local, and networking APIs are not available yet. Read [what PixelLang supports](docs/en/product.md) if you’re deciding whether it fits your project. Curious about the compiler? It is written in PixelLang itself; the [architecture guide](docs/en/internals/architecture.md) explains how it works.

## Contributing

Found a confusing example or an unexpected result? We welcome bug reports, clearer documentation and code contributions. The [contribution guide](docs/en/contributing/README.md) will help you get started.

## License

[Apache License 2.0](LICENSE).
