import unittest
from pixellang.spatial_values import CoordinateFrame, SpatialValue, SpaceValue, SpatialError


class NativeSpatialLinkTests(unittest.TestCase):
    def setUp(self):
        self.frame = CoordinateFrame('World', 'meters')
        self.world = SpaceValue('room', self.frame, 'str', 'int')
        self.world = self.world.insert('start', self.point(0.0), 'start').insert('end', self.point(2.0), 'end')
        self.a, self.b = self.world.entity_id('start'), self.world.entity_id('end')

    def point(self, z):
        return SpatialValue('Point3', self.frame, (0.0, 0.0, z))

    def test_links_survive_motion_and_preserve_historical_payloads(self):
        linked = self.world.connect('road', self.a, self.b, 5)
        edge = linked.require_link(linked.link_id('road'))
        moved = linked.move(self.b, self.point(9.0))
        updated = moved.set_link_payload(edge.id, 8)
        self.assertEqual(updated.require(edge.target_id).position.xyz[2], 9.0)
        self.assertEqual(linked.require(edge.target_id).position.xyz[2], 2.0)
        self.assertEqual(edge.payload, 5)
        self.assertEqual(updated.require_link(updated.link_id('road')).payload, 8)
        self.assertEqual(self.world.links, ())
        self.assertEqual(updated.require(edge.source_id).id, self.a)

    def test_direction_parallel_links_and_self_links_are_explicit(self):
        world = self.world.connect('one', self.a, self.b, 1).connect('two', self.a, self.b, 2).connect('loop', self.b, self.b, 3)
        self.assertEqual([e.identity for e in world.outgoing(self.a)], ['one', 'two'])
        self.assertEqual([e.identity for e in world.incoming(self.a)], [])
        self.assertEqual([e.identity for e in world.incoming(self.b)], ['one', 'two', 'loop'])
        self.assertEqual([e.identity for e in world.outgoing(self.b)], ['loop'])

    def test_remove_rejects_dangling_links_without_changing_snapshot(self):
        linked = self.world.connect('road', self.a, self.b, 5)
        with self.assertRaisesRegex(SpatialError, 'disconnect'):
            linked.remove(self.b)
        edge = linked.require_link(linked.link_id('road'))
        cleaned = linked.disconnect(edge.id).remove(self.b)
        self.assertEqual(len(cleaned.entities), 1)
        self.assertEqual(len(linked.entities), 2)
        with self.assertRaises(SpatialError): cleaned.connect('new', self.a, self.b, 7)
        relinked = linked.disconnect(edge.id).connect('new', self.a, self.b, 7)
        self.assertEqual(relinked.links[0].identity, 'new')
        self.assertIsNone(relinked.get_link(edge.id))

    def test_foreign_endpoints_and_link_references_are_rejected(self):
        other = SpaceValue('other', self.frame, 'str', 'int').insert('other', self.point(1.0), 'other')
        foreign = other.entity_id('other')
        edge = other.connect('road', foreign, foreign, 1).link_id('road')
        with self.assertRaises(SpatialError): self.world.connect('road', self.a, foreign, 2)
        with self.assertRaises(SpatialError): self.world.disconnect(edge)
        with self.assertRaises(SpatialError): self.world.get_link(edge)
