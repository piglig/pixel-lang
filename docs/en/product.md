# What is PixelLang?

[简体中文](../zh-CN/product.md) · [English](product.md) · [Documentation](README.md)

PixelLang lets you write a program, explore how it behaves, and share it as an image. It is a spatial programming language: positions and connections can be part of what a program computes.

## Start with a problem you know

You can use PixelLang for familiar tasks such as reading data, sorting records and building a program across several files. You can also describe things with a place in space: nearby objects, routes between locations, or the floors served by an elevator.

For programs that react to events, a `flow` remembers state between steps. An order can remember that it has been paid, and a controller can decide what to do next from its current state and a new input.

Try the [order example](../../examples/native-orders/README.en.md), [spatial elevator](../../examples/native-elevator/README.en.md) or [data report](../../examples/ledger/README.en.md) to see these ideas in use.

## Share a program as a PNG

Write and test your code, then export it as a PNG. Someone with PixelLang installed can run that image with new input or recover its code into an editable project.

The pixels carry the program’s structure. That is why you need to share the original image without resizing or editing it. Recovery keeps the program’s behavior, but not its original comments or formatting. Input data and permissions to read files are supplied separately when the program runs.

You can also work entirely with text files. Exporting an image is a way to share a program, not a required step every time you run it.

## Explore space and state

X, Y and Z describe where things are. Move an entity and a nearby-entity query can return a different answer. Connections can carry data, such as a route’s cost.

Time is separate from position. A flow advances when you give it an input; it reads the previous state and commits its updates together. In Studio, you can inspect values and revisit recorded steps. Rotating the camera or rearranging the graph only changes your view.

The [spatial guide](guides/spatial-programming.md) and [state-machine guide](guides/state-machines.md) walk through working examples.

## Is it ready for my project?

PixelLang is in development preview. Syntax and file formats are still evolving, so it is a good time to experiment and give feedback.

The toolchain currently supports macOS and Linux. You’ll need Python 3.11+ and a matching native compiler bundle; follow the [installation guide](guides/installation.md) to prepare them. Dependencies are local, and there is no network package registry or language-level networking API yet.

The compiler is written in PixelLang. CLI and Studio use its native executable, while ordinary programs run and debug in PixelVM. If you want to work on the implementation, start with the [architecture](internals/architecture.md).

## Try it

Follow [your first program](learn/getting-started.md) to go from a text file to an executable image and back again. Prefer an editor? Set up [VS Code Studio](guides/studio.md).
