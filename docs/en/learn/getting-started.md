# Your first program

[简体中文](../../zh-CN/learn/getting-started.md) · [English](getting-started.md) · [Documentation](../README.md)

Write and run a text program, export it as PNG, then recover editable source.
First complete [installation](../guides/installation.md) and confirm `pixel doctor` succeeds. Commands below use an activated toolchain environment; a source installation can also use entries such as `uv run pixelrun`.

## 1. Write and run

Create `hello.pxl`:

```pixellang
effect fn main() {
    print("Hello, PixelLang!")
}
```

```sh
pixelrun hello.pxl
```

```text
Hello, PixelLang!
```

`main` is the entry point. Printing is an external effect, so the entry uses `effect fn`.

## 2. Use collection algorithms

Replace the file contents with:

```pixellang
import "std/collections.pxl" as collections

effect fn main() {
    let values = collections.Sorted([9, 2, 7])
    for value in values {
        print(value)
    }
}
```

Run it again to print `2`, `7`, then `9`. `Sorted` returns a sorted array and `let` creates a readonly binding. Use `var` for local updates; other copies remain unchanged.

## 3. Deliver an image

```sh
pixelpack hello.pxl -o program.png
pixelrun program.png
pixelunpack program.png -o recovered
pixelrun recovered/hello.pxl
```

All three executions should agree. Use an empty recovery directory. PNG includes the entry and reachable modules, but execution still requires the PixelLang runtime. Recovery produces canonical source without original comments or formatting. Preserve exact pixels when transferring the image.

## Next steps

- [Projects](../guides/projects.md): manifests, dependencies and tests.
- [Language overview](../reference/language/overview.md): types, declarations and control flow.
- [Spatial programming](../guides/spatial-programming.md): positions and connections that participate in computation.
