# Weighted routes

[简体中文](README.md) · [English](README.en.md) · [Documentation](../../docs/en/README.md)

Propagate distances along typed links and compare results after a link update.

## Run

Complete [installation](../../docs/en/guides/installation.md), then run from the repository root:

```sh
pixelrun examples/weighted-routes/main.pxl
```

## Expected output

```text
[0, 5, 20, -1]
[0, 5, 7, 21]
[0, 5, 7, 8]
[0, 5, 20, 1]
false
```

## Key contracts

Road{cost, enabled} stores weight and enabled state. The first three steps propagate A → B → C → D; enabling A → D and creating a new instance gives distance 1. -1 means unreachable. This teaching example uses four consecutive identities and nonnegative weights, without general negative-cycle detection. Recreate the instance after link changes. Costs come from weights, not geometric distance.

## Deliver and recover

```sh
pixelpack examples/weighted-routes/main.pxl -o weighted-routes.png
pixelrun weighted-routes.png
pixelunpack weighted-routes.png -o weighted-routes-restored
pixelrun weighted-routes-restored/main.pxl
```

Use an empty recovery directory. Image execution and recovered source should match the original. See [space](../../docs/en/reference/language/spatial.md) · [dataflow](../../docs/en/internals/dataflow.md)。
