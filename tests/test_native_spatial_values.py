import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError

from pixellang.spatial_values import (
    CoordinateFrame, SpatialValue, SpatialError, Transform,
    add, subtract, scale, dot, distance,
    SpaceValue, FrozenValue,
)

IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


class NativeSpatialValuesTests(unittest.TestCase):
    def test_deepcopy_shares_native_values_but_not_mutable_host_containers(self):
        scene = SpaceValue('room', self.world, 'int[]', 'str').insert(
            self.point((1.0, 2.0, 3.0)), FrozenValue('int[]', (1, 2, 3)))
        source = {'scene': scene, 'mutable': [1]}
        copied = deepcopy(source)
        self.assertIs(copied['scene'], scene)
        copied['mutable'].append(2)
        self.assertEqual(source['mutable'], [1])
        moved = scene.move(scene.get(0), self.point((4.0, 5.0, 6.0)))
        self.assertEqual(copied['scene'].get(0).position.xyz, (1.0, 2.0, 3.0))
        self.assertIs(moved.get(0).payload, scene.get(0).payload)

    def test_native_snapshots_reject_transitively_mutable_payloads(self):
        from pixellang.jsonvalue import JsonValue
        for items in ([1], ([1],), (('nested', {'value': 1}),), (JsonValue('array', []),)):
            with self.subTest(items=items), self.assertRaises(SpatialError):
                FrozenValue('int[]', items)
        with self.assertRaises(SpatialError):
            SpaceValue('room', self.world, 'int[]', 'str').insert(self.point((0.0, 0.0, 0.0)), [1])
    def setUp(self):
        self.world = CoordinateFrame('World', 'meters')
        self.cabin = CoordinateFrame('Cabin', 'centimeters')

    def point(self, xyz, frame=None):
        return SpatialValue('Point3', frame or self.world, xyz)

    def vector(self, xyz, frame=None):
        return SpatialValue('Vec3', frame or self.world, xyz)

    def test_position_and_displacement_algebra(self):
        a, b = self.point((1.0, 2.0, 3.0)), self.point((4.0, 6.0, 3.0))
        displacement = subtract(b, a)
        self.assertEqual(displacement, self.vector((3.0, 4.0, 0.0)))
        self.assertEqual(add(a, displacement), b)
        self.assertEqual(add(displacement, a), b)
        self.assertEqual(distance(a, b), 5.0)
        self.assertEqual(dot(displacement, displacement), 25.0)
        self.assertEqual(scale(displacement, 2.0).xyz, (6.0, 8.0, 0.0))
        with self.assertRaises(SpatialError): add(a, b)
        with self.assertRaises(SpatialError): subtract(displacement, a)
        with self.assertRaises(SpatialError): scale(a, 2.0)

    def test_frames_are_nominal_and_values_immutable(self):
        a = self.point((0.0, 0.0, 0.0))
        b = self.point((0.0, 0.0, 0.0), CoordinateFrame('Other', 'meters'))
        with self.assertRaises(SpatialError): distance(a, b)
        with self.assertRaises(FrozenInstanceError): a.xyz = (1.0, 1.0, 1.0)
        for xyz in ((float('nan'), 0.0, 0.0), (1, 0.0, 0.0), [0.0, 0.0, 0.0]):
            with self.subTest(xyz=xyz), self.assertRaises(SpatialError): self.point(xyz)
        with self.assertRaises(SpatialError):
            add(self.vector((1e308, 0.0, 0.0)), self.vector((1e308, 0.0, 0.0)))

    def test_transform_respects_units_directions_and_composition(self):
        first = Transform(self.cabin, self.world, IDENTITY, (10.0, 0.0, 0.0))
        self.assertEqual(first.apply(self.point((100.0, 0.0, 0.0), self.cabin)),
                         self.point((11.0, 0.0, 0.0)))
        self.assertEqual(first.apply(self.vector((100.0, 0.0, 0.0), self.cabin)),
                         self.vector((1.0, 0.0, 0.0)))
        target = CoordinateFrame('Target', 'millimeters')
        rotation = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        second = Transform(self.world, target, rotation, (0.0, 0.0, 20.0))
        p = self.point((200.0, 50.0, 30.0), self.cabin)
        self.assertEqual(first.then(second).apply(p), second.apply(first.apply(p)))
        with self.assertRaises(SpatialError): first.apply(self.point((0.0, 0.0, 0.0)))
        with self.assertRaises(SpatialError): second.then(first)
        with self.assertRaises(SpatialError):
            Transform(self.world, self.cabin, ((-1.0, 0.0, 0.0), *IDENTITY[1:]), (0.0, 0.0, 0.0))

    def test_z_changes_neighborhood_without_changing_previous_snapshot(self):
        empty = SpaceValue('room', self.world, 'str', 'str')
        original = empty.insert(self.point((0.0, 0.0, 0.0)), 'origin')
        original = original.insert(self.point((0.0, 0.0, 2.0)), 'near')
        original = original.insert(self.point((0.0, 0.0, 5.0)), 'far')
        origin, moving = original.get(0), original.get(1)
        self.assertEqual([e.identity for e in original.neighbors(origin, 2.0)], [1])
        changed = original.move(moving, self.point((0.0, 0.0, 3.0)))
        self.assertEqual(changed.neighbors(origin, 2.0), ())
        self.assertEqual(original.get(1).position.xyz[2], 2.0)
        self.assertEqual(len(empty.entities), 0)
        # Old references resolve by identity in the queried snapshot.
        self.assertEqual(changed.current(moving).position.xyz[2], 3.0)
        self.assertTrue(changed.ahead(origin, moving, self.vector((0.0, 0.0, 1.0)), 0.0))
        self.assertFalse(changed.ahead(origin, moving, self.vector((1.0, 0.0, 0.0)), 0.0))

    def test_deleted_ids_are_not_reused_and_foreign_entities_are_rejected(self):
        a = SpaceValue('a', self.world, 'int', 'str').insert(self.point((0.0, 0.0, 0.0)), 1)
        b = SpaceValue('b', self.world, 'int', 'str').insert(self.point((0.0, 0.0, 0.0)), 2)
        for action in (lambda: a.move(b.get(0), self.point((1.0, 0.0, 0.0))),
                       lambda: a.neighbors(b.get(0), 3.0), lambda: a.remove(b.get(0))):
            with self.assertRaises(SpatialError): action()
        removed = a.remove(a.get(0))
        inserted = removed.insert(self.point((1.0, 0.0, 0.0)), 3)
        self.assertEqual(inserted.entities[0].identity, 1)
        with self.assertRaises(SpatialError): inserted.get(0)
        with self.assertRaises(SpatialError): inserted.neighbors(inserted.get(1), -1.0)
