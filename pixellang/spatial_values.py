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
    'spaceInsert': 4, 'spaceGet': 2, 'spaceMove': 3, 'spaceRemove': 2,
    'spaceEntityId': 2, 'spaceLinkId': 2, 'identityKey': 1,
    'spaceRequire': 2, 'spaceRequireLink': 2, 'spaceSetPayload': 3, 'spaceRemoveCascade': 2,
    'linkSourceId': 1, 'linkTargetId': 1,
    'spaceNeighbors': 3, 'spaceAhead': 5, 'spaceSize': 1,
    'entityPosition': 1, 'entityPayload': 1, 'entityId': 1,
    'distance3': 2, 'dot3': 2,
    'transformPoint': 2, 'transformVector': 2, 'transformThen': 2,
    'spaceConnect': 5, 'spaceDisconnect': 2, 'spaceGetLink': 2, 'spaceSetLinkPayload': 3,
    'spaceOutgoing': 2, 'spaceIncoming': 2, 'spaceLinkCount': 1,
    'linkId': 1, 'linkPayload': 1,
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


def business_key(key):
    if type(key) is not str or not key or len(key) > 256:
        raise SpatialError('Spatial keys must be nonempty strings of at most 256 characters')
    return key


@dataclass(frozen=True, slots=True)
class EntityId(ImmutableNative):
    owner: str
    key: str
    frame: CoordinateFrame
    payload_type: str

    def __post_init__(self):
        ImmutableNative.__post_init__(self)
        business_key(self.key)


@dataclass(frozen=True, slots=True)
class LinkId(ImmutableNative):
    owner: str
    key: str
    frame: CoordinateFrame
    entity_type: str
    payload_type: str

    def __post_init__(self):
        ImmutableNative.__post_init__(self)
        business_key(self.key)


@dataclass(frozen=True, slots=True)
class EntityValue(ImmutableNative):
    owner: str
    identity: str
    position: SpatialValue
    payload_type: str
    payload: object

    @property
    def id(self):
        return EntityId(self.owner, self.identity, self.position.frame, self.payload_type)


@dataclass(frozen=True, slots=True)
class LinkValue(ImmutableNative):
    owner: str
    identity: str
    frame: CoordinateFrame
    entity_type: str
    payload_type: str
    source: str
    target: str
    payload: object

    @property
    def id(self):
        return LinkId(self.owner, self.identity, self.frame, self.entity_type, self.payload_type)

    @property
    def source_id(self):
        return EntityId(self.owner, self.source, self.frame, self.entity_type)

    @property
    def target_id(self):
        return EntityId(self.owner, self.target, self.frame, self.entity_type)


@dataclass(frozen=True, slots=True)
class SpaceValue(ImmutableNative):
    identity: str
    frame: CoordinateFrame
    payload_type: str
    link_type: str
    entities: tuple[EntityValue, ...] = ()
    version: int = 0
    links: tuple[LinkValue, ...] = ()

    def point(self, position):
        if (not isinstance(position, SpatialValue) or position.kind != 'Point3'
                or position.frame != self.frame):
            raise SpatialError('Entity position requires the space coordinate frame')

    def entity_id(self, key):
        return EntityId(self.identity, business_key(key), self.frame, self.payload_type)

    def link_id(self, key):
        return LinkId(self.identity, business_key(key), self.frame, self.payload_type, self.link_type)

    def check_entity_id(self, identity):
        if (type(identity) is not EntityId or identity.owner != self.identity
                or identity.frame != self.frame or identity.payload_type != self.payload_type):
            raise SpatialError('Expected an entity identity belonging to this space')

    def check_link_id(self, identity):
        if (type(identity) is not LinkId or identity.owner != self.identity
                or identity.frame != self.frame or identity.entity_type != self.payload_type
                or identity.payload_type != self.link_type):
            raise SpatialError('Expected a link identity belonging to this space')

    def get(self, identity):
        self.check_entity_id(identity)
        return next((entity for entity in self.entities if entity.identity == identity.key), None)

    def require(self, identity):
        entity = self.get(identity)
        if entity is None: raise SpatialError('Entity does not exist in this snapshot')
        return entity

    def insert(self, key, position, payload):
        identity = self.entity_id(key)
        self.point(position)
        if self.get(identity) is not None: raise SpatialError('Entity key already exists in this snapshot')
        entity = EntityValue(self.identity, key, position, self.payload_type, payload)
        return replace(self, entities=self.entities + (entity,), version=self.version + 1)

    def move(self, identity, position):
        entity = self.require(identity)
        self.point(position)
        updated = replace(entity, position=position)
        return replace(self, entities=tuple(updated if e.identity == identity.key else e for e in self.entities),
                       version=self.version + 1)

    def set_payload(self, identity, payload):
        entity = self.require(identity)
        updated = replace(entity, payload=payload)
        return replace(self, entities=tuple(updated if e.identity == identity.key else e for e in self.entities),
                       version=self.version + 1)

    def remove(self, identity, *, cascade=False):
        self.require(identity)
        incident = tuple(link for link in self.links if link.source == identity.key or link.target == identity.key)
        if incident and not cascade:
            raise SpatialError('Entity has incident links; disconnect them or use removeCascade')
        return replace(self, entities=tuple(e for e in self.entities if e.identity != identity.key),
                       links=tuple(link for link in self.links if link.source != identity.key and link.target != identity.key),
                       version=self.version + 1)

    def get_link(self, identity):
        self.check_link_id(identity)
        return next((link for link in self.links if link.identity == identity.key), None)

    def require_link(self, identity):
        link = self.get_link(identity)
        if link is None: raise SpatialError('Link does not exist in this snapshot')
        return link

    def connect(self, key, source, target, payload):
        identity = self.link_id(key)
        if self.get_link(identity) is not None: raise SpatialError('Link key already exists in this snapshot')
        self.require(source); self.require(target)
        link = LinkValue(self.identity, key, self.frame, self.payload_type,
                         self.link_type, source.key, target.key, payload)
        return replace(self, links=self.links + (link,), version=self.version + 1)

    def disconnect(self, identity):
        self.require_link(identity)
        return replace(self, links=tuple(item for item in self.links if item.identity != identity.key),
                       version=self.version + 1)

    def set_link_payload(self, identity, payload):
        link = self.require_link(identity)
        updated = replace(link, payload=payload)
        return replace(self, links=tuple(updated if item.identity == identity.key else item for item in self.links),
                       version=self.version + 1)

    def outgoing(self, identity):
        self.require(identity)
        return tuple(link for link in self.links if link.source == identity.key)

    def incoming(self, identity):
        self.require(identity)
        return tuple(link for link in self.links if link.target == identity.key)

    def neighbors(self, identity, radius):
        origin = self.require(identity)
        if type(radius) is not float or not math.isfinite(radius) or radius < 0.0:
            raise SpatialError('Neighborhood radius must be finite and nonnegative')
        return tuple(e for e in self.entities
                     if e.identity != identity.key and distance(origin.position, e.position) <= radius)

    def ahead(self, origin, candidate, direction, tolerance):
        a, b = self.require(origin), self.require(candidate)
        if (not isinstance(direction, SpatialValue) or direction.kind != 'Vec3'
                or direction.frame != self.frame or direction.xyz == (0.0, 0.0, 0.0)):
            raise SpatialError('Direction must be a nonzero displacement in the space frame')
        if type(tolerance) is not float or not math.isfinite(tolerance) or tolerance < 0.0:
            raise SpatialError('Direction tolerance must be finite and nonnegative')
        length = math.hypot(*direction.xyz)
        if not math.isfinite(length): raise SpatialError('Direction magnitude exceeds finite range')
        normalized = SpatialValue('Vec3', self.frame, tuple(v / length for v in direction.xyz))
        return dot(subtract(b.position, a.position), normalized) > tolerance


_NATIVE_VALUE_TYPES = (CoordinateFrame, SpatialOrigin, SpatialValue, Transform,
                       FrozenValue, EntityId, LinkId, EntityValue, LinkValue, SpaceValue)
