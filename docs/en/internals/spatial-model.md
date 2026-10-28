# Spatial model implementation

[简体中文](../../zh-CN/internals/spatial-model.md) · [English](spatial-model.md) · [Documentation](../README.md)

Spatial types belong to the language and runtime, not renderer JSON or graph layout. Public operation contracts live in the [spatial reference](../reference/language/spatial.md).

## Types and identities

`frame` declares a nominal coordinate system whose unit participates in semantics. Point3, Vec3 and Transform3 retain frame types; the compiler rejects implicit mixing. Space carries frame, entity-payload and link-payload types. Entities and links have stable identities independent of display names or coordinate equality.

Entity references identify their owning space. Queries resolve them by identity in a specified snapshot; an entity value retains its original position and payload. Deleted identities are not reused. Foreign-space references and dangling links are rejected.

## Snapshots and updates

Operations return new snapshots with isolated typed payloads. Parallel edges and self-loops are valid; stable identities determine query order. Moving an entity preserves links, and removal requires explicitly disconnecting incident links first. The runtime validates positions, budgets and identities before publishing results. Failure does not partially mutate input.

Flows store space snapshots as ordinary state. Execution faults roll back a step; transition functions decide how business Result.Err affects state. Checkpoints preserve the identity registry and resource accounting so resumed execution obeys the same boundaries.

## Queries and transforms

Radius neighborhoods, directional projections and explicit links are distinct relationships. Spatial indexes must match direct queries, including boundaries, ordering and invalid-input behavior. Translation affects positions, not directions. Unit conversion and transform composition are explicit. Different floating-point implementations are not promised bitwise agreement across hardware.

## Source and Studio

The compiler retains Unicode scalar ranges for coordinate expressions; the host converts these to UTF-16 edits. Provenance does not participate in equality or hashing. Direct literals support coordinate editing; computed positions navigate to expressions instead of becoming constants. Coordinate equality is not a provenance heuristic.

Camera, zoom and pagination affect presentation only. Source edits affect every execution of an expression; runtime events update runtime state only. Stale snapshots cannot write source. Real dragging and keyboard Undo require separate interaction checks; see [Studio](../guides/studio.md).

## Delivery and verification

PNG preserves recoverable types, coordinates, links and expressions. A 3D model file is not a substitute for program delivery. Editing recovered Z coordinates or link payloads should affect output; moving the camera should not.

Checks cover frame mismatch rejection, unit conversion, snapshot isolation, budget recovery, PNG round-trips and installed editor behavior. See [layered verification](../contributing/verification.md).
