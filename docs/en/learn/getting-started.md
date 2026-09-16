# Your first program

[中文](../../zh-CN/learn/getting-started.md) · [English](getting-started.md) · [Documentation](../README.md)

At the repository root run `uv sync --locked`; Python 3.11+ is required. Save this as hello.pxl:

```pixellang
fn main() {
    print("Hello, PixelLang!")
}
```

```sh
.venv/bin/pixelrun hello.pxl
```

## Arrays and sorting

```pixellang
import "std/sort.pxl" as sort

fn main() {
    let values = sort.Ints([9, 2, 7])
    for index, value in values {
        print(string(index) + ": " + string(value))
    }
}
```

let prevents rebinding but container contents remain mutable; use var for reassignment.
Functions and variables are statically checked. Imports use relative paths or std/ for libraries.

## Multiple files and delivery

The [data-report example](../../../examples/data-report/main.pxl) reads CSV, sorts and computes statistics:

```sh
.venv/bin/pixel test examples/data-report
.venv/bin/pixel build examples/data-report
.venv/bin/pixel run examples/data-report/dist/program.png --input examples/data-report/measurements.csv
```

The PNG holds the semantic-pixel program. `pixelunpack program.png -o empty-directory`
recovers editable files without original comments/formatting. Continue with
[projects](../guides/projects.md), [language](../reference/language/overview.md) and
[standard library](../reference/standard-library.md).
