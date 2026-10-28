# Spatial programming

[简体中文](../../zh-CN/guides/spatial-programming.md) · [English](spatial-programming.md) · [Documentation](../README.md)

Create a 3D space with typed links, move an entity, compare snapshots, then export and recover the program. Complete installation and the first-program tutorial first.


**On this page**

- [Write a program](#write-a-program)
- [Run, deliver and recover](#run-deliver-and-recover)
- [Inspect in Studio](#inspect-in-studio)
- [Further use](#further-use)
- [Further reading](#further-reading)

## Write a program

Save the following as `main.pxl`. X, Y and Z are spatial coordinates; time is independent.

```pixellang
frame World = meters
record Road { cost: int, enabled: bool }

fn Count(network: Space[World, string, Road]) -> int {
    let origin = network.get(0)
    return len(network.neighbors(origin, 2.0))
}
flow Inspect() step(network: Space[World, string, Road]) -> int {
    let count = Count(network)
    output count
}
effect fn main() {
    var network = Space::[World, string, Road]("room")
    network = network.insert(Point3::[World]{x: 0.0, y: 0.0, z: 0.0}, "observer")
    network = network.insert(Point3::[World]{x: 0.0, y: 0.0, z: 1.5}, "moving")
    network = network.connect(network.get(0), network.get(1), Road{cost: 7, enabled: true})
    let before = network
    network = network.move(network.get(1), Point3::[World]{x: 0.0, y: 0.0, z: 3.0})
    print(Inspect().step(before))
    print(Inspect().step(network))
    let road = network.outgoing(network.get(0))[0]
    print(road.payload.cost)
    print(network.target(road).position.z)
}
```

`frame` names a coordinate frame and declares its length unit.
`Space[World, string, Road]` specifies the frame, entity payload type and link payload
type. Entities have stable identities. Moving an entity produces a new snapshot;
`before` retains its old position. Explicit links follow endpoint identities and
survive movement.

A `fn` encapsulates pure computation; a `flow` expresses dependencies through
expressions and named results. `Count(network)` computes a result, while
`network.connect(a, b, payload)` creates a relation between spatial entities.
The layout of computations in Studio does not change spatial distances.

## Run, deliver and recover

```sh
pixel run main.pxl --json
pixel pack main.pxl -o room.png
pixel run room.png --json
pixel unpack room.png -o restored --json
pixel run restored/main.pxl --json
```

All three `run` commands should return `output: [1, 0, 7, 3.0]`: the old space has
one neighbor, while the moved space has none. The link still has weight 7, and
resolving its target in the new snapshot gives Z=3. `pack` preserves recoverable
native program semantics; the PNG is more than a screenshot. Use a fresh directory
for recovery.

Change `cost: 7` to `cost: 9` in the recovered source and run it again. The third
output should become 9. Coordinates, link payloads and types remain editable.

## Inspect in Studio

Open the source in VS Code and run **PixelLang: Open Native Graph**. Set a breakpoint
on the return statement in `Count` and start PixelLang debugging. The paused native
graph inspector shows the current flow input; click its **Space** button to view
entities and directed links.

After selecting an entity, **Orbit** rotates the camera. **Move X/Y/Z** can edit a
position literal with a direct source origin through pointer dragging. An edit
invalidates the old debug view; restart debugging to run the new program. Focus the
source editor and use its usual undo shortcut to revert. Computed positions cannot
be overwritten through dragging. Use **Reveal expression** to navigate to the
arithmetic or coordinate transform that produced the position.

## Further use

- Frames cannot mix implicitly. Positions use `Point3[F]`, displacements use `Vec3[F]`, and transforms use `Transform3[From, To]`.
- Link payloads can contain records and containers. Extracted containers have value semantics: later local updates preserve the original snapshot and history.
- Stateful flows use `state`, `next`, explicit constructors and `instance.step(...)`; see [native dataflow](../internals/dataflow.md).
- The [weighted routes example](../../../examples/weighted-routes/README.en.md) shows link state and weights driving propagation across steps.
- Full operation contracts are in the [native spatial model](../reference/language/spatial.md#native-spatial-interface).

- The [native elevator example](../../../examples/native-elevator/README.en.md) combines multiple source files, stateful flows, 3D neighborhoods and typed docking links, including failed-step rollback and independent instances.

## Further reading

- [Documentation index](../README.md)
- [Install the toolchain](installation.md)
