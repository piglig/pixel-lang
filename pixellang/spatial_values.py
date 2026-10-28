"""Native immutable spatial values and operations used by the VM.

Frame identity and units are semantic data, independent of scene rendering.
"""
from dataclasses import dataclass, field, replace, fields
import math


class SpatialError(ValueError):
    pass


class ImmutableNative:
    """Validated immutable graphs can be shared by VM checkpoints and replay."""
    __slots__ = ()

    def __post_init__(self):
        from .jsonvalue import JsonValue
        if type(self) not in _NATIVE_VALUE_TYPES:
            raise SpatialError('Native snapshot types cannot be extended')
        pending = [getattr(self, item.name) for item in fields(self)]
        visited = set()
        while pending:
            value = pending.pop()
            if value is None or type(value) in (str, int, float, bool, bytes):
                continue
            if type(value) in _NATIVE_VALUE_TYPES:
                continue  # Every child validated its own fields at construction.
            identity = id(value)
            if identity in visited:
                continue
            visited.add(identity)
            if type(value) is tuple:
                pending.extend(value)
            elif type(value) is JsonValue:
                pending.extend((value.tag, value.data))
            else:
                raise SpatialError('Native snapshots require immutable payloads and fields')

    def __deepcopy__(self, memo):
        return self


UNITS = {'meters': 1.0, 'centimeters': 0.01, 'millimeters': 0.001}
OPERATIONS = {
    'spaceInsert': 3, 'spaceGet': 2, 'spaceMove': 3, 'spaceRemove': 2,
    'spaceNeighbors': 3, 'spaceAhead': 5, 'spaceSize': 1,
    'entityPosition': 1, 'entityPayload': 1, 'entityId': 1,
    'distance3': 2, 'dot3': 2,
    'transformPoint': 2, 'transformVector': 2, 'transformThen': 2,
    'spaceConnect': 4, 'spaceDisconnect': 2, 'spaceGetLink': 2, 'spaceSetLinkPayload': 3,
    'spaceOutgoing': 2, 'spaceIncoming': 2, 'spaceLinkCount': 1,
    'linkId': 1, 'linkPayload': 1, 'linkSource': 2, 'linkTarget': 2,
}


@dataclass(frozen=True, slots=True)
class CoordinateFrame(ImmutableNative):
    identity: str
    unit: str

    def __post_init__(self):
        ImmutableNative.__post_init__(self)
        if not isinstance(self.identity, str) or not self.identity or not isinstance(self.unit, str) or self.unit not in UNITS:
            raise SpatialError('Invalid coordinate frame identity or unit')


def coordinates(values):
    if (not isinstance(values, tuple) or len(values) != 3
            or any(type(v) is not float or not math.isfinite(v) for v in values)):
        raise SpatialError('Spatial coordinates require three finite float64 values')
    return values


@dataclass(frozen=True, slots=True)
class SpatialOrigin(ImmutableNative):
    function: str
    node: int


@dataclass(frozen=True, slots=True)
class SpatialValue(ImmutableNative):
    kind: str
    frame: CoordinateFrame
    xyz: tuple[float, float, float]
    origin: SpatialOrigin | None = field(default=None, compare=False, repr=False)

    def __post_init__(self):
        ImmutableNative.__post_init__(self)
        if self.kind not in ('Point3', 'Vec3') or not isinstance(self.frame, CoordinateFrame):
            raise SpatialError('Invalid spatial value type')
        coordinates(self.xyz)


def same_frame(a, b):
    if not isinstance(a, SpatialValue) or not isinstance(b, SpatialValue):
        raise SpatialError('Expected spatial operands')
    if a.frame != b.frame:
        raise SpatialError('Spatial operands require the same coordinate frame')


def add(a, b, *, origin=None):
    same_frame(a, b)
    if a.kind == b.kind == 'Point3':
        raise SpatialError('Cannot add two positions')
    kind = 'Point3' if 'Point3' in (a.kind, b.kind) else 'Vec3'
    return SpatialValue(kind, a.frame, tuple(x + y for x, y in zip(a.xyz, b.xyz)), origin)


def subtract(a, b, *, origin=None):
    same_frame(a, b)
    if a.kind == 'Vec3' and b.kind == 'Point3':
        raise SpatialError('Cannot subtract a position from a displacement')
    kind = 'Vec3' if a.kind == b.kind else 'Point3'
    return SpatialValue(kind, a.frame, tuple(x - y for x, y in zip(a.xyz, b.xyz)), origin)


def scale(value, factor, *, origin=None):
    if not isinstance(value, SpatialValue) or value.kind != 'Vec3':
        raise SpatialError('Scaling requires a displacement')
    if type(factor) is not float or not math.isfinite(factor):
        raise SpatialError('Scale requires a finite float64')
    return SpatialValue('Vec3', value.frame, tuple(x * factor for x in value.xyz), origin)


def dot(a, b):
    same_frame(a, b)
    if a.kind != 'Vec3' or b.kind != 'Vec3':
        raise SpatialError('Dot product requires displacements')
    result = sum(x * y for x, y in zip(a.xyz, b.xyz))
    if not math.isfinite(result):
        raise SpatialError('Non-finite spatial result')
    return result


def distance(a, b):
    same_frame(a, b)
    if a.kind != 'Point3' or b.kind != 'Point3':
        raise SpatialError('Distance requires positions')
    result = math.dist(a.xyz, b.xyz)
    if not math.isfinite(result):
        raise SpatialError('Non-finite spatial result')
    return result


@dataclass(frozen=True, slots=True)
class Transform(ImmutableNative):
    """Rigid rotation plus translation; unit conversion is explicit in frames.

    Matrix is row-major. Translation is measured in target-frame units.
    """
    source: CoordinateFrame
    target: CoordinateFrame
    rotation: tuple[tuple[float, float, float], ...]
    translation: tuple[float, float, float]

    def __post_init__(self):
        ImmutableNative.__post_init__(self)
        if not isinstance(self.source, CoordinateFrame) or not isinstance(self.target, CoordinateFrame):
            raise SpatialError('Transform requires coordinate frames')
        coordinates(self.translation)
        if not isinstance(self.rotation, tuple) or len(self.rotation) != 3:
            raise SpatialError('Transform requires a 3 by 3 rotation')
        for row in self.rotation:
            coordinates(row)
        for i in range(3):
            for j in range(3):
                product = sum(a * b for a, b in zip(self.rotation[i], self.rotation[j]))
                if abs(product - float(i == j)) > 1e-12:
                    raise SpatialError('Transform rotation must be orthonormal')
        a, b, c = self.rotation
        determinant = (a[0] * (b[1]*c[2] - b[2]*c[1])
                       - a[1] * (b[0]*c[2] - b[2]*c[0])
                       + a[2] * (b[0]*c[1] - b[1]*c[0]))
        if abs(determinant - 1.0) > 1e-12:
            raise SpatialError('Transform rotation must preserve handedness')

    def apply(self, value, *, origin=None):
        if not isinstance(value, SpatialValue) or value.frame != self.source:
            raise SpatialError('Transform source frame does not match value')
        ratio = UNITS[self.source.unit] / UNITS[self.target.unit]
        xyz = tuple(sum(a * b for a, b in zip(row, value.xyz)) * ratio
                    + (self.translation[i] if value.kind == 'Point3' else 0.0)
                    for i, row in enumerate(self.rotation))
        return SpatialValue(value.kind, self.target, xyz, origin)

    def then(self, after):
        if not isinstance(after, Transform) or self.target != after.source:
            raise SpatialError('Transform composition frames do not match')
        rotation = tuple(tuple(sum(after.rotation[i][k] * self.rotation[k][j]
                                   for k in range(3)) for j in range(3)) for i in range(3))
        translation = after.apply(SpatialValue('Point3', self.target, self.translation)).xyz
        return Transform(self.source, after.target, rotation, translation)


@dataclass(frozen=True, slots=True)
class FrozenValue(ImmutableNative):
    """A typed copy of a VM heap object, with no mutable heap references."""
    type_name: str
    items: tuple


@dataclass(frozen=True, slots=True)
class EntityValue(ImmutableNative):
    owner: str
    identity: int
    position: SpatialValue
    payload_type: str
    payload: object


@dataclass(frozen=True, slots=True)
class LinkValue(ImmutableNative):
    owner: str
    identity: int
    frame: CoordinateFrame
    entity_type: str
    payload_type: str
    source: int
    target: int
    payload: object


@dataclass(frozen=True, slots=True)
class SpaceValue(ImmutableNative):
    identity: str
    frame: CoordinateFrame
    payload_type: str
    link_type: str
    entities: tuple[EntityValue, ...] = ()
    next_identity: int = 0
    version: int = 0
    links: tuple[LinkValue, ...] = ()
    next_link_identity: int = 0

    def point(self, position):
        if (not isinstance(position, SpatialValue) or position.kind != 'Point3'
                or position.frame != self.frame):
            raise SpatialError('Entity position requires the space coordinate frame')

    def get(self, identity):
        if type(identity) is not int:
            raise SpatialError('Entity identity must be an integer')
        for entity in self.entities:
            if entity.identity == identity:
                return entity
        raise SpatialError('Entity does not exist in this snapshot')

    def current(self, entity):
        if (not isinstance(entity, EntityValue) or entity.owner != self.identity
                or entity.payload_type != self.payload_type or entity.position.frame != self.frame):
            raise SpatialError('Entity belongs to a different space')
        # A reference is an identity, not permission to inject a stale position.
        return self.get(entity.identity)

    def insert(self, position, payload):
        self.point(position)
        if self.next_identity >= 2**63 - 1:
            raise SpatialError('Entity identity limit exceeded')
        entity = EntityValue(self.identity, self.next_identity, position, self.payload_type, payload)
        return replace(self, entities=self.entities + (entity,),
                       next_identity=self.next_identity + 1, version=self.version + 1)

    def move(self, entity, position):
        entity = self.current(entity)
        self.point(position)
        updated = EntityValue(entity.owner, entity.identity, position, entity.payload_type, entity.payload)
        return replace(self, entities=tuple(updated if e.identity == entity.identity else e for e in self.entities),
                       version=self.version + 1)

    def remove(self, entity):
        entity = self.current(entity)
        if any(link.source == entity.identity or link.target == entity.identity for link in self.links):
            raise SpatialError('Disconnect incident links before removing an entity')
        return replace(self, entities=tuple(e for e in self.entities if e.identity != entity.identity),
                       version=self.version + 1)

    def get_link(self, identity):
        if type(identity) is not int:
            raise SpatialError('Link identity must be an integer')
        for link in self.links:
            if link.identity == identity:
                return link
        raise SpatialError('Link does not exist in this snapshot')

    def current_link(self, link):
        if (not isinstance(link, LinkValue) or link.owner != self.identity or link.frame != self.frame
                or link.entity_type != self.payload_type or link.payload_type != self.link_type):
            raise SpatialError('Link belongs to a different space')
        return self.get_link(link.identity)

    def connect(self, source, target, payload):
        source, target = self.current(source), self.current(target)
        if self.next_link_identity >= 2**63 - 1:
            raise SpatialError('Link identity limit exceeded')
        link = LinkValue(self.identity, self.next_link_identity, self.frame, self.payload_type,
                         self.link_type, source.identity, target.identity, payload)
        return replace(self, links=self.links + (link,), next_link_identity=self.next_link_identity + 1,
                       version=self.version + 1)

    def disconnect(self, link):
        link = self.current_link(link)
        return replace(self, links=tuple(item for item in self.links if item.identity != link.identity),
                       version=self.version + 1)

    def set_link_payload(self, link, payload):
        link = self.current_link(link)
        updated = replace(link, payload=payload)
        return replace(self, links=tuple(updated if item.identity == link.identity else item for item in self.links),
                       version=self.version + 1)

    def outgoing(self, entity):
        entity = self.current(entity)
        return tuple(link for link in self.links if link.source == entity.identity)

    def incoming(self, entity):
        entity = self.current(entity)
        return tuple(link for link in self.links if link.target == entity.identity)

    def link_source(self, link):
        return self.get(self.current_link(link).source)

    def link_target(self, link):
        return self.get(self.current_link(link).target)

    def neighbors(self, entity, radius):
        origin = self.current(entity)
        if type(radius) is not float or not math.isfinite(radius) or radius < 0.0:
            raise SpatialError('Neighborhood radius must be finite and nonnegative')
        return tuple(e for e in self.entities
                     if e.identity != origin.identity and distance(origin.position, e.position) <= radius)

    def ahead(self, origin, candidate, direction, tolerance):
        a, b = self.current(origin), self.current(candidate)
        if (not isinstance(direction, SpatialValue) or direction.kind != 'Vec3'
                or direction.frame != self.frame or direction.xyz == (0.0, 0.0, 0.0)):
            raise SpatialError('Direction must be a nonzero displacement in the space frame')
        if type(tolerance) is not float or not math.isfinite(tolerance) or tolerance < 0.0:
            raise SpatialError('Direction tolerance must be finite and nonnegative')
        length = math.hypot(*direction.xyz)
        if not math.isfinite(length):
            raise SpatialError('Direction magnitude exceeds finite range')
        normalized = SpatialValue('Vec3', self.frame, tuple(v / length for v in direction.xyz))
        return dot(subtract(b.position, a.position), normalized) > tolerance


_NATIVE_VALUE_TYPES = (CoordinateFrame, SpatialOrigin, SpatialValue, Transform,
                       FrozenValue, EntityValue, LinkValue, SpaceValue)
