# 3D neighborhoods

[简体中文](README.md) · [English](README.en.md) · [Documentation](../../docs/en/README.md)

Change only an entity’s Z coordinate and observe a different computational result.

## Run

Complete [installation](../../docs/en/guides/installation.md), then run from the repository root:

```sh
pixelrun examples/native-space/main.pxl
```

## Expected output

```text
1
0
2.0
3.0
true
```

## Key contracts

Point3[World] is a position and Vec3[World] a displacement. Movement returns a new snapshot without changing the old one. neighbors includes the radius boundary, excludes the origin and returns results in insertion order. Entity values retain their original positions; space queries accept typed EntityId values and resolve them in the specified snapshot.

## Deliver and recover

```sh
pixelpack examples/native-space/main.pxl -o native-space.png
pixelrun native-space.png
pixelunpack native-space.png -o native-space-restored
pixelrun native-space-restored/main.pxl
```

Use an empty recovery directory. Image execution and recovered source should match the original. See [space](../../docs/en/reference/language/spatial.md) · [dataflow](../../docs/en/internals/dataflow.md)。
