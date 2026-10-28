# Native spatial elevator

[简体中文](README.md) · [English](README.en.md) · [Documentation](../../docs/en/README.md)

Run this example with the current toolchain. Complete [installation](../../docs/en/guides/installation.md); commands below run from the repository root.

A four-floor elevator uses PixelLang `fn`, `flow` and `Space` directly.
Controller state and the 3D scene commit atomically in one flow step. Floors are
3 meters apart; each `Tick` moves one floor.

- `model.pxl` declares the coordinate frame, events, controller state and output.
- `controller.pxl` implements transitions, spatial updates and neighborhood queries.
- `main.pxl` composes three pure functions with ordinary expressions and exercises independent instances, a rejected
  event and historical snapshots.

## Run

The example now uses expression flows. Run these commands after the bundled compiler is regenerated:

```sh
pixel run examples/native-elevator --json
pixel pack examples/native-elevator/main.pxl -o elevator.png
pixel run elevator.png --json
pixel unpack elevator.png -o elevator-restored --json
pixel run elevator-restored/main.pxl --json
```

Each successful event prints three values: `[current floor, target floor, docking
link count]`, whether the doors are open, and neighboring floor identities.

| Event | Current / target | Doors | Nearby floors | Docking links |
| --- | --- | --- | --- | --- |
| Request floor 3 | 0 / 3 | Closed | `[0]` | 0 |
| Open during trip | Rejected; state unchanged | Closed | — | — |
| Tick | 1 / 3 | Closed | `[1]` | 0 |
| Tick | 2 / 3 | Closed | `[2]` | 0 |
| Tick | 3 / 3 | Open | `[3]` | 1 |
| Request floor 1 | 3 / 1 | Closed | `[3]` | 0 |
| Tick | 2 / 1 | Closed | `[2]` | 0 |
| Tick | 1 / 1 | Open | `[1]` | 1 |
| Open second instance | 0 / 0 | Open | `[0]` | 1 |

The final `9.0` and `1` come from the earlier floor-3 snapshot: later moves preserve
its position and link. The rejected event prints `rejected: Cannot open doors during
a trip`; the next Tick continues from the original state. Requested floors must be
between 0 and 3.

The flow returns `Result[View, Rejection]`. `InvalidFloor(floor)`, `DoorsOpen` and
`InTransit` are typed business rejections handled with `match`. A rejected step explicitly
retains status and scene while completing the step; execution faults still abort the whole
step. An invalid first request never reads a cabin entity that has not been created.

## Spatial semantics and debugging

Floor entities have identities 0–3 and the cabin has identity 4, scoped to each
independent space instance. Y is height, Z is spatial, and logical steps are separate
from XYZ. `space.neighbors(...)` finds floors within 1.1 meters. Open doors at a neighboring
target floor create a directed docking link carrying a bool payload.

Open `main.pxl` in VS Code and run **PixelLang: Open Native Graph**. Set a breakpoint
in `Inspect` and start debugging to inspect the controller, old/staged spatial
snapshots, entities and docking links, or step back through earlier execution.

After recovering the source, change only the cabin movement's literal Z coordinate
in `Arrange` to `2.0`: neighboring floors and docking links disappear because their
geometric distances change. The recovery test locates that coordinate through native
compiler source ranges and preserves the computed Y expression.
