# Independent counters

[简体中文](README.md) · [English](README.en.md) · [Documentation](../../docs/en/README.md)

Create two flow instances and observe independent state accumulation.

## Run

Complete [installation](../../docs/en/guides/installation.md), then run from the repository root:

```sh
pixelrun examples/native-counter/main.pxl
```

## Expected output

```text
1
3
10
6
```

## Key contracts

Counter() creates an instance; step(delta) reads old state and commits atomically. Flow[fn(int) -> int] can be passed as a parameter; copying a handle does not create another counter.

## Deliver and recover

```sh
pixelpack examples/native-counter/main.pxl -o native-counter.png
pixelrun native-counter.png
pixelunpack native-counter.png -o native-counter-restored
pixelrun native-counter-restored/main.pxl
```

Use an empty recovery directory. Image execution and recovered source should match the original. See [space](../../docs/en/reference/language/spatial.md) · [dataflow](../../docs/en/internals/dataflow.md)。
