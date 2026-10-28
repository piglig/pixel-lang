# Expression data pipeline

[简体中文](README.md) · [English](README.en.md) · [Documentation](../../docs/en/README.md)

Organize array doubling, summation and composition through explicit dependencies.

## Run

Complete [installation](../../docs/en/guides/installation.md), then run from the repository root:

```sh
pixelrun examples/native-pipeline/main.pxl
```

## Expected output

```text
24
16
```

## Key contracts

lib.pxl defines Pipeline. Combined declares two persistent child instances and combines their step outputs. A flow call constructs an instance even without state; use fn for ordinary stateless computation. Dependency sorting evaluates prerequisites without requiring declaration and execution order to match.

## Deliver and recover

```sh
pixelpack examples/native-pipeline/main.pxl -o native-pipeline.png
pixelrun native-pipeline.png
pixelunpack native-pipeline.png -o native-pipeline-restored
pixelrun native-pipeline-restored/main.pxl
```

Use an empty recovery directory. Image execution and recovered source should match the original. See [space](../../docs/en/reference/language/spatial.md) · [dataflow](../../docs/en/internals/dataflow.md)。
