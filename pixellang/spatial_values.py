"""Native immutable spatial values and operations used by the VM.

Frame identity and units are semantic data, independent of scene rendering.
"""
from dataclasses import dataclass, field, replace, fields
import math
from collections.abc import Sequence
from .value_map import ValueMap
from .spatial_storage import allocation_cost


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


@dataclass(frozen=True, slots=True, init=False, eq=False)
class SpatialTable(ImmutableNative, Sequence):
    """Validated immutable, insertion-ordered values with a business-key index.

    Public construction validates entries once. Updates only accept immutable
    native children and share the existing trusted storage.
    """
    _data: ValueMap
    allocated: int = field(compare=False, repr=False)

    def __init__(self, entries=()):
        data = ValueMap()
        for key, value in entries:
            self._validate(key, value)
            if key in data:
                raise SpatialError('Repeated spatial table key')
            data = data.set(key, value)
        object.__setattr__(self, '_data', data)
        object.__setattr__(self, 'allocated', allocation_cost(data))

    @staticmethod
    def _validate(key, value):
        business_key(key)
        if type(value) not in (EntityValue, LinkValue, SpatialTable, str):
            raise SpatialError('Invalid spatial table value')

    @classmethod
    def _updated(cls, data, previous):
        result = object.__new__(cls)
        object.__setattr__(result, '_data', data)
        object.__setattr__(result, 'allocated', allocation_cost(data, previous))
        return result

    def set(self, key, value):
        self._validate(key, value)
        return self._updated(self._data.set(key, value), self._data)

    def remove(self, key):
        return self._updated(self._data.remove(key), self._data)

    def get(self, key):
        return self._data.get(key)

    def items(self):
        return self._data.items()

    def __len__(self):
        return len(self._data)

    def __iter__(self):
        return self._data.values()

    def __getitem__(self, index):
        # Positional access is for debugger pagination, never identity lookup.
        from itertools import islice
        if isinstance(index, slice):
            return tuple(self)[index]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return next(islice(self, index, index + 1))

    def __eq__(self, other):
        if self is other:
            return True
        if type(other) is SpatialTable:
            return len(self) == len(other) and all(a == b for a, b in zip(self.items(), other.items()))
        return NotImplemented

    __hash__ = None


@dataclass(frozen=True, slots=True)
class SpaceValue(ImmutableNative):
    identity: str
    frame: CoordinateFrame
    payload_type: str
    link_type: str
    entities: SpatialTable = field(default_factory=SpatialTable)
    version: int = 0
    links: SpatialTable = field(default_factory=SpatialTable)
    outgoing_index: SpatialTable = field(default_factory=SpatialTable, compare=False, repr=False)
    incoming_index: SpatialTable = field(default_factory=SpatialTable, compare=False, repr=False)
    allocated: int = field(default=1, compare=False, repr=False)

    def __post_init__(self):
        ImmutableNative.__post_init__(self)
        if any(type(table) is not SpatialTable for table in
               (self.entities, self.links, self.outgoing_index, self.incoming_index)):
            raise SpatialError('Space storage requires immutable spatial tables')

    def _update(self, *, extra=0, **changes):
        cost = 1 + extra + sum(table.allocated + 1 for table in changes.values())
        return replace(self, **changes, version=self.version + 1, allocated=cost)

    @staticmethod
    def _add_edge(index, endpoint, key):
        bucket = index.get(endpoint)
        if bucket is None:
            bucket = SpatialTable()
        bucket = bucket.set(key, key)
        return index.set(endpoint, bucket), bucket.allocated + 1

    @staticmethod
    def _drop_edge(index, endpoint, key):
        bucket = index.get(endpoint).remove(key)
        changed = index.set(endpoint, bucket) if bucket else index.remove(endpoint)
        return changed, bucket.allocated + 1

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
        return self.entities.get(identity.key)

    def require(self, identity):
        entity = self.get(identity)
        if entity is None: raise SpatialError('Entity does not exist in this snapshot')
        return entity

    def insert(self, key, position, payload):
        identity = self.entity_id(key)
        self.point(position)
        if self.get(identity) is not None: raise SpatialError('Entity key already exists in this snapshot')
        entity = EntityValue(self.identity, key, position, self.payload_type, payload)
        return self._update(entities=self.entities.set(key, entity), extra=1)

    def move(self, identity, position):
        entity = self.require(identity)
        self.point(position)
        updated = replace(entity, position=position)
        return self._update(entities=self.entities.set(identity.key, updated), extra=1)

    def set_payload(self, identity, payload):
        entity = self.require(identity)
        updated = replace(entity, payload=payload)
        return self._update(entities=self.entities.set(identity.key, updated), extra=1)

    def remove(self, identity, *, cascade=False):
        self.require(identity)
        outgoing = self.outgoing_index.get(identity.key)
        incoming = self.incoming_index.get(identity.key)
        if (outgoing or incoming) and not cascade:
            raise SpatialError('Entity has incident links; disconnect them or use removeCascade')
        draft = self
        cost = 0
        # Dict preserves insertion order and removes the duplicate for self-loops.
        for key in dict.fromkeys((*(outgoing or ()), *(incoming or ()))):
            draft = draft.disconnect(self.link_id(key))
            cost += draft.allocated
        updated = draft._update(entities=draft.entities.remove(identity.key))
        return replace(updated, version=self.version + 1, allocated=cost + updated.allocated)

    def get_link(self, identity):
        self.check_link_id(identity)
        return self.links.get(identity.key)

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
        outgoing, out_cost = self._add_edge(self.outgoing_index, source.key, key)
        incoming, in_cost = self._add_edge(self.incoming_index, target.key, key)
        return self._update(links=self.links.set(key, link), outgoing_index=outgoing,
                            incoming_index=incoming, extra=1 + out_cost + in_cost)

    def disconnect(self, identity):
        link = self.require_link(identity)
        outgoing, out_cost = self._drop_edge(self.outgoing_index, link.source, identity.key)
        incoming, in_cost = self._drop_edge(self.incoming_index, link.target, identity.key)
        return self._update(links=self.links.remove(identity.key), outgoing_index=outgoing,
                            incoming_index=incoming, extra=out_cost + in_cost)

    def set_link_payload(self, identity, payload):
        link = self.require_link(identity)
        updated = replace(link, payload=payload)
        return self._update(links=self.links.set(identity.key, updated), extra=1)

    def outgoing(self, identity):
        self.require(identity)
        return tuple(self.links.get(key) for key in (self.outgoing_index.get(identity.key) or ()))

    def incoming(self, identity):
        self.require(identity)
        return tuple(self.links.get(key) for key in (self.incoming_index.get(identity.key) or ()))

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
                       FrozenValue, EntityId, LinkId, EntityValue, LinkValue, SpatialTable, SpaceValue)
