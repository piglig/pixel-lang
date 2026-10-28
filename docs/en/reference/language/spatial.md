# Space and source layout

[简体中文](../../../zh-CN/reference/language/spatial.md) · [English](spatial.md) · [Documentation](../../README.md)

Distinguish computational space, logical time, pixel-source structure and editor layout before using spatial semantics.


**On this page**

- [Contract and interface](#contract-and-interface)
- [Native spatial interface](#native-spatial-interface)
- [Explicit compact regions](#explicit-compact-regions)
- [Host scheduling](#host-scheduling)
- [Coordinate and geometry constraints](#coordinate-and-geometry-constraints)
- [Further reading](#further-reading)

## Contract and interface

PixelLang distinguishes four models without implicit conversion between them:

| Model | Affects execution | Meaning |
| --- | --- | --- |
| Native space XYZ | Yes | `frame`, `Point3`, `Vec3`, entities and typed links participate in computation; see the [native spatial model](../../internals/spatial-model.md). |
| Logical time T | Yes | Events and a fixed interval advance flow state independently of XYZ; see [event applications](../../guides/state-machines.md). |
| Semantic pixel source layout | Yes | Two-dimensional PNG pixels encode token order and syntax using the rules below. |
| Editor view layout | No | Component graph positions, camera, zoom and history presentation do not change the program. |

The nonnegative integer `(X,Y)` coordinates below refer only to PNG source pixels, not to the program's native three-dimensional space. A history view may display time along an axis; this does not give time semantics to native spatial Z.

## Native spatial interface

Frames and payload types come from the receiver without repeated type arguments.

| Operation | Behavior |
| --- | --- |
| `Space::[F, T, L](identity)` | Empty snapshot; identity must be unique in a run |
| `space.insert(position, payload)` | Retain a payload value snapshot, allocate a monotonic ID, return a snapshot |
| `space.get(id)` | Read an entity; missing IDs cause execution faults |
| `space.move(entity, position)` | Move by identity and return a new snapshot |
| `space.remove(entity)` | Delete without reusing its identity |
| `space.neighbors(entity, radius)` | Inclusive Euclidean neighborhood, excluding self |
| `space.ahead(origin, candidate, direction, tolerance)` | Direction query; tolerance uses the space length unit |
| `space.size()` | Entity count |
| `space.connect(source, target, payload)` | Retain a payload value snapshot, allocate a directed link identity, return a snapshot |
| `space.getLink(id)` | Read a link; deleted identities are never reused |
| `space.disconnect(link)` | Remove the specified link and return a snapshot |
| `space.setLinkPayload(link, payload)` | Replace payload while retaining identity and endpoints |
| `space.outgoing(entity)` / `space.incoming(entity)` | Outgoing/incoming links in identity order |
| `space.linkCount()` | Link count |
| `space.source(link)` / `space.target(link)` | Resolve endpoints in the supplied snapshot, including their positions at that time |
| `link.id` / `link.payload` | Read identity or payload; payloads are independent values of their declared types |
| `entity.id` / `entity.position` / `entity.payload` | Read entity values; payloads are independent values of their declared types |
| `point.distance(other)` / `vector.dot(other)` | Same-frame position distance and displacement dot product |
| `transform.point(point)` / `transform.vector(vector)` | Explicit point or direction conversion |
| `first.then(after)` | Apply first, then after |

`ahead` normalizes the direction and checks whether the displacement projected onto it is strictly greater
than `tolerance`; this parameter is not a query radius. Disconnect all incident links before removing an entity.
Spatial methods return values without modifying receivers: `let disconnected = scene.disconnect(link)` leaves `scene` intact.

Assignment, collection mutation and `inout` cannot write through properties. `var data = link.payload` creates an
independently editable local value; `scene.setLinkPayload(link, data)` produces an updated snapshot. Entity properties
read the position and payload stored in that entity value. Space queries and moves resolve its identity in the receiver snapshot.

## Explicit compact regions

A `region` connection points from a semantic token to itself and marks that token
as a region head. Every semantic token must belong to an explicitly marked region; unmarked nonempty sources are rejected:

1. Markers are unique. Region order is head order by `(Y, X)`, permitting several
   statements on the same physical row.
2. On a row without `next` links, consecutive tokens follow increasing X, with
   distance at most three; a new marked head starts a new region.
3. If a row contains any `next` link, every region on that row uses explicit chains.
   Heads cannot have incoming links; chains cannot branch, merge, cycle or cross
   rows. All semantic tokens must belong to exactly one marked region.
4. Regions default to module scope. Only `child` links establish containment; the
   parent must precede the child in region order, including when both share Y.
   Each child has at most one parent, and only grammar block openers can contain
   child statements. Indentation does not infer scope in this mode.

The PixelLang emitter packs complete statement regions into shelves, retaining
token order and explicit parent relationships. Consecutive tokens need no redundant
`next` edges. Markers and parent edges are structural source connections; no AST,
text program or bytecode is stored in image annotations. All existing pixel and
dimension limits remain enforced. Physical indentation never determines scope. Moving a region requires moving its link endpoints too.

## Host scheduling

Application scheduling is configured by the host after main initializes the flow. It does not interpret XYZ as time. See [native event applications](../../guides/state-machines.md).

## Coordinate and geometry constraints

Coordinates use a right-handed frame with Y up and finite float64 components. Units are meters, centimeters or millimeters; distinct frames remain distinct types even with the same unit. Point3 represents position and Vec3 displacement: point minus point yields a vector, point plus vector yields a point, and point plus point is invalid.
Radius queries include the boundary, exclude the origin entity and order results by stable entity identity. Radius must be finite and nonnegative.

`Transform3[From, To]` has a 3×3 orthogonal `rotation` matrix and `translation: Vec3[To]`. Transforms cannot change handedness or introduce implicit scaling; frame units determine unit conversion. `first.then(after)` applies first before after. Parallel links and self-loops are valid. Deleted identities are never reused; foreign or missing identities cause execution faults.

## Further reading

- [Documentation index](../../README.md)
- [Language overview](overview.md)
