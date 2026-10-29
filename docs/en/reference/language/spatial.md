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

An identity answers **which object**; an entity or link value contains **data from one snapshot**. Use `EntityId[F, T]` and `LinkId[F, T, L]` for queries and updates. These types support equality and map keys. They cannot be constructed from raw strings: obtain them from the owning space.

| Operation | Behavior |
| --- | --- |
| `Space::[F, T, L](identity)` | Create an empty space; its identity is unique in a run |
| `space.entityId(key) / space.linkId(key)` | Create a typed identity without inserting an object |
| `space.insert(key, position, payload)` | Insert an entity; reject duplicate keys |
| `space.get(id) / space.getLink(id)` | Return Option[Entity] / Option[Link]; absent means None |
| `space.require(id) / space.requireLink(id)` | Read an object that must exist; absence is an execution fault |
| `space.move(id, position) / space.setPayload(id, payload)` | Return an updated snapshot with the same entity identity |
| `space.remove(id)` | Remove an entity only when no incident links remain |
| `space.removeCascade(id)` | Remove an entity and all incident links together |
| `space.neighbors(id, radius)` | Return nearby entities from this snapshot, excluding self |
| `space.ahead(originId, candidateId, direction, tolerance)` | Test the projected displacement along a direction |
| `space.connect(key, sourceId, targetId, payload)` | Insert a directed link between existing entities |
| `space.disconnect(id) / space.setLinkPayload(id, payload)` | Remove a link or replace its payload |
| `space.outgoing(id) / space.incoming(id)` | Return links in insertion order |
| `space.size() / space.linkCount()` | Count entities / links |
| `entity.id / entity.position / entity.payload` | Read the identity and data captured by this entity value |
| `link.id / link.source / link.target / link.payload` | Read link identity, endpoint identities and captured payload |
| `id.key` | Read the stable business key |
| `point.distance(other) / vector.dot(other)` | Same-frame distance / dot product |
| `transform.point(point) / transform.vector(vector)` | Convert positions / displacements explicitly |
| `first.then(after)` | Apply first, then after |

Business keys are exact, case-sensitive strings of 1–256 characters. A key is scoped to its space and identity type. Two branches inserting the same key describe the same logical object; different keys remain different even at identical positions. Re-inserting a removed key deliberately recreates that logical identity. Use a new key for a new object.

All updates return new snapshots. Stored entity and link values keep their original data. To read an endpoint in a particular snapshot, use `space.get(link.target)` or `space.require(link.target)`. Passing a captured entity value where an identity is required is a type error; pass `entity.id` explicitly.

Foreign-space identities, duplicate insertions and updates to missing objects are execution faults. `get` and `getLink` return `None` for valid identities absent from the selected snapshot. Removing an entity is explicit: disconnect its links first, or use `removeCascade`.

Properties are readonly, including through containers and `inout`. Copy a payload into a local variable to edit it, then publish it with `setPayload` or `setLinkPayload`. Earlier snapshots remain unchanged.

`ahead` normalizes the direction and tests whether projected displacement is strictly greater than `tolerance`, measured in the frame's length unit.

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
Radius queries include the boundary, exclude the origin entity and return results in insertion order. Radius must be finite and nonnegative.

`Transform3[From, To]` has a 3×3 orthogonal `rotation` matrix and `translation: Vec3[To]`. Transforms cannot change handedness or introduce implicit scaling; frame units determine unit conversion. `first.then(after)` applies first before after. Parallel links and self-loops are valid. Identity lookup and absence follow the contracts above.

## Further reading

- [Documentation index](../../README.md)
- [Language overview](overview.md)
