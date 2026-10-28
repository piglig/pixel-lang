# PixelLang documentation

[简体中文](../zh-CN/README.md) · [English](README.md) · [Documentation](README.md)

Start with a running program, then choose a task guide. Use references for exact behavior and internals for compiler work.

| Your goal | Start with |
| --- | --- |
| Try PixelLang | [Install](guides/installation.md) → [First program](learn/getting-started.md) |
| Develop an application | [Projects](guides/projects.md) → [Studio](guides/studio.md) |
| Explore distinctive features | [Spatial programming](guides/spatial-programming.md) · [State machines](guides/state-machines.md) |
| Contribute code | [Architecture](internals/architecture.md) → [Contributing](contributing/README.md) |

## Start here

- [Project scope](product.md)
- [Installation](guides/installation.md)
- [Your first program](learn/getting-started.md)

## Build something

- [Projects, dependencies and tests](guides/projects.md)
- [VS Code Studio](guides/studio.md)
- [Command-line tools](guides/cli.md)
- [Spatial programming](guides/spatial-programming.md)
- [State machines and event applications](guides/state-machines.md)

## Language reference

- [Syntax overview](reference/language/overview.md)
- [Numbers](reference/language/numbers.md)
- [Values and update permissions](reference/language/data.md)
- [Functions, effects and closures](reference/language/functions.md)
- [Generics and capabilities](reference/language/generics.md)
- [Enums, Option and pattern matching](reference/language/enums.md)
- [Result and error propagation](reference/language/errors.md)
- [Typed JSON](reference/language/json.md)
- [Collections and algorithms](reference/language/collections.md)
- [Space and source layout](reference/language/spatial.md)

## APIs and formats

- [Standard library](reference/standard-library.md)
- [Compiler service protocol](reference/compiler-service.md)
- [File types](reference/formats/files.md)
- [Semantic-pixel encoding](reference/formats/encoding.md)
- [Executable PNG](reference/formats/png.md)
- [PixelVM bytecode](reference/formats/bytecode.md)
- [Runtime and replay](reference/formats/runtime.md)

## Understand the implementation

- [Architecture](internals/architecture.md)
- [Design principles and future work](internals/design.md)
- [Compiler interfaces](internals/compiler-interfaces.md)
- [IR and function reuse](internals/ir.md)
- [Value storage](internals/value-storage.md)
- [Spatial model implementation](internals/spatial-model.md)
- [Dataflow and transactions](internals/dataflow.md)
- [Python responsibilities](internals/python.md)
- [Self-hosting and source recovery](internals/self-hosting.md)

## Contribute

- [Contributing](contributing/README.md)
- [Layered verification](contributing/verification.md)
- [Conformance coverage](contributing/conformance.md)
- [Performance measurement](contributing/performance.md)
