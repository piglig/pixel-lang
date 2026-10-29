# Your first program

[简体中文](../../zh-CN/learn/getting-started.md) · [English](getting-started.md) · [Documentation](../README.md)

Let’s write a small program, run it, and turn it into an image. At the end, you’ll recover the code from that image and run it again.
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

PixelLang starts running at `main`. Because this function prints to the terminal, it uses `effect fn`: a function allowed to interact with the outside world.

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

You should see the same output when you run the source, the PNG and the recovered code. Choose an empty folder for `recovered`. The image includes the code your program needs, and the person running it will need PixelLang installed. Share the original PNG without resizing or editing it; recovered source keeps the program’s behavior, but not its original comments or formatting.

## Next steps

- [Projects](../guides/projects.md): manifests, dependencies and tests.
- [Language overview](../reference/language/overview.md): types, declarations and control flow.
- [Spatial programming](../guides/spatial-programming.md): positions and connections that participate in computation.
