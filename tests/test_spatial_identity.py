"""Identity is stable across forks; entity data always belongs to a snapshot."""
import unittest
from pixellang.spatial_values import CoordinateFrame, SpatialValue, SpaceValue, SpatialError


class SpatialIdentityTests(unittest.TestCase):
    def setUp(self):
        self.frame = CoordinateFrame('World', 'meters')
        self.empty = SpaceValue('room', self.frame, 'str', 'int')
        self.point = lambda z: SpatialValue('Point3', self.frame, (0.0, 0.0, float(z)))

    def test_identity_resolves_only_in_explicit_snapshot(self):
        key = self.empty.entity_id('alice')
        self.assertIsNone(self.empty.get(key))
        before = self.empty.insert('alice', self.point(0), 'old')
        old = before.require(key)
        after = before.move(key, self.point(3)).set_payload(key, 'new')
        self.assertEqual(old.position.xyz[2], 0)
        self.assertEqual(old.payload, 'old')
        self.assertEqual(after.require(key).position.xyz[2], 3)
        self.assertEqual(after.require(key).payload, 'new')
        self.assertEqual(old.id, after.require(key).id)
        with self.assertRaises(SpatialError):after.move(old, self.point(4))

    def test_forks_share_logical_keys_not_automatic_allocation_slots(self):
        left = self.empty.insert('alice', self.point(1), 'left')
        right = self.empty.insert('bob', self.point(2), 'right')
        self.assertIsNone(right.get(left.entity_id('alice')))
        self.assertIsNone(left.get(right.entity_id('bob')))
        same = self.empty.insert('alice', self.point(9), 'other branch')
        self.assertEqual(left.require(left.entity_id('alice')).id, same.entity_id('alice'))
        self.assertNotEqual(left.require(left.entity_id('alice')).position, same.require(same.entity_id('alice')).position)

    def test_connections_and_explicit_cascade_preserve_old_snapshot(self):
        space = self.empty.insert('a', self.point(0), 'a').insert('b', self.point(1), 'b')
        a,b = space.entity_id('a'),space.entity_id('b')
        space = space.connect('road', a, b, 7).connect('loop', a, a, 8)
        road = space.link_id('road')
        self.assertEqual(space.require_link(road).source_id, a)
        with self.assertRaises(SpatialError):space.remove(a)
        after = space.remove(a, cascade=True)
        self.assertIsNone(after.get(a))
        self.assertIsNone(after.get_link(road))
        self.assertEqual(len(space.links),2)
        self.assertEqual(after.require(b).payload,'b')
        self.assertEqual(space.set_link_payload(road,9).require_link(road).payload,9)
        self.assertEqual(space.require_link(road).payload,7)

    def test_duplicates_foreign_ids_and_invalid_keys_are_rejected(self):
        space = self.empty.insert('a', self.point(0), 'a')
        with self.assertRaises(SpatialError):space.insert('a',self.point(1),'other')
        foreign = SpaceValue('other', self.frame, 'str', 'int').entity_id('a')
        with self.assertRaises(SpatialError):space.get(foreign)
        for key in ('', 'x'*257, 1):
            with self.subTest(key=key),self.assertRaises(SpatialError):space.entity_id(key)
        removed=space.remove(space.entity_id('a'))
        # Reusing a business key deliberately recreates the same logical identity.
        recreated=removed.insert('a',self.point(5),'recreated')
        self.assertEqual(recreated.require(space.entity_id('a')).payload,'recreated')

    def test_identity_map_keys_count_business_key_bytes(self):
        from unittest.mock import patch
        from pixellang import map_keys
        from pixellang.model import PixelError
        from pixellang.vm import VM
        from tests.test_native_spatial_vm import program
        vm = VM(program())
        space = SpaceValue('room', CoordinateFrame('@0:0', 'meters'), 'int', 'str')
        for identity in (space.entity_id('名' * 80), space.link_id('名' * 80)):
            with patch.object(map_keys, 'MAX_KEY_BYTES', 100), self.assertRaises(PixelError):
                map_keys.token(vm, identity, vm.value_type(identity))

if __name__=='__main__':unittest.main()
