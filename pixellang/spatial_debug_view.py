"""Read native spatial snapshots for Studio without changing the program or VM."""
import math

from .debugvalues import resolve_value, describe_value
from .model import PixelError
from .spatial_values import SpaceValue, EntityValue, distance


def space_view(timeline, args):
    vm, path, value = resolve_value(timeline, args)
    if not isinstance(value, (SpaceValue, EntityValue)):
        raise PixelError('debug', 'Select a native Space or Entity value')
    start, count = args.get('start', 0), args.get('count', 100)
    if type(start) is not int or start < 0 or type(count) is not int or not 1 <= count <= 100:
        raise PixelError('debug', 'Invalid spatial page')
    space = value if isinstance(value, SpaceValue) else None
    frame = space.frame if space is not None else value.position.frame
    origin = None
    query = args.get('neighbors')
    if query is not None:
        if space is None or not isinstance(query, dict):
            raise PixelError('debug', 'Neighborhood inspection requires a native Space')
        identity, radius = query.get('origin'), query.get('radius')
        try:
            valid_radius = type(radius) in (float, int) and radius >= 0 and math.isfinite(radius)
        except OverflowError:
            valid_radius = False
        if type(identity) is not str or not identity or not valid_radius:
            raise PixelError('debug', 'Neighborhood requires an entity identity and finite nonnegative radius')
        origin = next((entity for entity in space.entities if entity.identity == identity), None)
        if origin is None:
            raise PixelError('debug', 'Entity does not exist in this snapshot')
    result, total = [], 0
    entities = space.entities if space is not None else (value,)
    for index, entity in enumerate(entities):
        separation = distance(origin.position, entity.position) if origin is not None else None
        if origin is not None and (entity.identity == origin.identity or separation > radius):
            continue
        if start <= total < start + count:
            entity_path = path + ['entities', index] if space is not None else path
            origin_info = None
            provenance = entity.position.origin
            if provenance is not None:
                debug = getattr(timeline.compiled, 'debug', None) or {}
                info = debug.get('functions', {}).get(provenance.function)
                if info:
                    origin_info = dict(source=info['source'], node=provenance.node)
            result.append(dict(origin=origin_info, id=entity.identity, owner=entity.owner, position=list(entity.position.xyz),
                               distance=separation,
                               payload=describe_value(timeline, vm, entity.payload, entity_path + ['payload'], 'payload')))
        total += 1
    visible = {entity['id'] for entity in result}
    links, visible_links = [], 0
    if space is not None:
        for index, link in enumerate(space.links):
            if link.source not in visible or link.target not in visible:
                continue
            visible_links += 1
            if len(links) < 100:
                links.append(dict(id=link.identity, source=link.source, target=link.target,
                                  payload=describe_value(timeline, vm, link.payload,
                                                         path + ['links', index, 'payload'], 'payload')))
    return dict(time=timeline.cursor, path=path, frame=frame.identity, unit=frame.unit,
                identity=space.identity if space is not None else value.owner,
                version=space.version if space is not None else None,
                start=start, count=count, total=total, entities=result,
                links=links, visibleLinkTotal=visible_links,
                linkTotal=len(space.links) if space is not None else 0)
