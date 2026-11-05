# Spatial model implementation

[简体中文](../../zh-CN/internals/spatial-model.md) · [English](spatial-model.md) · [Documentation](../README.md)

Spatial types belong to the language and runtime, not renderer JSON or graph layout. Public operation contracts live in the [spatial reference](../reference/language/spatial.md).

## Types and identities

`frame` declares a nominal coordinate system whose unit participates in semantics. Point3, Vec3 and Transform3 retain frame types; the compiler rejects implicit mixing. Space carries frame, entity-payload and link-payload types. Entities and links have stable identities independent of display names or coordinate equality.

`EntityId[F,T]` and `LinkId[F,T,L]` contain an owning space and a stable business key, independent of snapshot version. Entity and link values contain captured data. Operations accept typed identities, never captured data as an implicit reference. Same-key insertions across forks denote the same logical object. Reusing a removed key is explicit recreation; lookup absence returns Option. Foreign-space identities are rejected.

## Snapshots and updates

Operations return new snapshots with isolated typed payloads. Parallel edges and self-loops are valid; insertion order determines query order. Moving an entity preserves links, and removal requires disconnecting incident links first or choosing removeCascade. The runtime validates positions, budgets and identities before publishing results. Failure does not partially mutate input.

Flows store space snapshots as ordinary state. Execution faults roll back a step; transition functions decide how business Result.Err affects state. Checkpoints preserve the identity registry and resource accounting so resumed execution obeys the same boundaries.

## Persistent storage

Entity and link tables use the runtime's insertion-ordered persistent maps: a hash
prefix index provides identity lookup and a radix-32 vector preserves iteration
order. An update copies the changed paths and shares untouched branches. Validated
immutable payloads are checked when created, not by scanning every entity on each
space update. Deleted order slots are compacted when they dominate live entries.

Separate persistent incoming and outgoing indexes map an entity key to its ordered
link keys. Payload and position updates leave those indexes unchanged. Queries
resolve current link values from the snapshot, so payload edits cannot leave stale
links in adjacency results. Cascade removal visits incident links, including
parallel edges and self-loops, and publishes one snapshot version.

Batch transition functions can repeatedly update a local snapshot and publish only
the final value. Intermediate snapshots share storage; rejected batches leave their
input untouched. There is no additional transaction syntax or mutable builder.
Point edits and identity lookup avoid whole-table scans; neighborhood radius queries
still scan entities. Deletion compaction occasionally rebuilds a table.

Native allocation accounting charges copied storage paths and new values. At idle
event boundaries, live traversal counts shared storage once across snapshots.
These units are resource accounting, not allocator bytes. Durable state checkpoints
use schema version 3; older checkpoint schemas are rejected. Logical table entries
are serialized in insertion order, independent of Python hash layout, and restored
indexes are validated against the entity/link tables. Serialization still visits
all entries of each distinct table; in-memory structural sharing is not a promise
of incremental disk checkpoint encoding.

## Queries and transforms

Radius neighborhoods, directional projections and explicit links are distinct relationships. Spatial indexes must match direct queries, including boundaries, ordering and invalid-input behavior. Translation affects positions, not directions. Unit conversion and transform composition are explicit. Different floating-point implementations are not promised bitwise agreement across hardware.

## Source and Studio

The compiler retains Unicode scalar ranges for coordinate expressions; the host converts these to UTF-16 edits. Provenance does not participate in equality or hashing. Direct literals support coordinate editing; computed positions navigate to expressions instead of becoming constants. Coordinate equality is not a provenance heuristic.

Camera, zoom and pagination affect presentation only. Source edits affect every execution of an expression; runtime events update runtime state only. Stale snapshots cannot write source. Real dragging and keyboard Undo require separate interaction checks; see [Studio](../guides/studio.md).

## Delivery and verification

PNG preserves recoverable types, coordinates, links and expressions. A 3D model file is not a substitute for program delivery. Editing recovered Z coordinates or link payloads should affect output; moving the camera should not.

Checks cover frame mismatch rejection, unit conversion, snapshot isolation, budget recovery, PNG round-trips and installed editor behavior. See [layered verification](../contributing/verification.md).
