import unittest
from pixellang.spatial_values import CoordinateFrame, SpatialValue, SpaceValue, SpatialError


class NativeSpatialLinkTests(unittest.TestCase):
    def setUp(self):
        self.frame = CoordinateFrame('World', 'meters')
        self.world = SpaceValue('room', self.frame, 'str', 'int')
        self.world = self.world.insert(self.point(0.0), 'start').insert(self.point(2.0), 'end')
        self.a, self.b = self.world.entities

    def point(self, z):
        return SpatialValue('Point3', self.frame, (0.0, 0.0, z))

    def test_links_survive_motion_and_preserve_historical_payloads(self):
        linked = self.world.connect(self.a, self.b, 5)
        edge = linked.get_link(0)
        moved = linked.move(self.b, self.point(9.0))
        updated = moved.set_link_payload(edge, 8)
        self.assertEqual(updated.link_target(edge).position.xyz[2], 9.0)
        self.assertEqual(linked.link_target(edge).position.xyz[2], 2.0)
        self.assertEqual(edge.payload, 5)
        self.assertEqual(updated.get_link(0).payload, 8)
        self.assertEqual(self.world.links, ())
        self.assertEqual(updated.link_source(edge).identity, self.a.identity)

    def test_direction_parallel_links_and_self_links_are_explicit(self):
        world = self.world.connect(self.a, self.b, 1).connect(self.a, self.b, 2).connect(self.b, self.b, 3)
        self.assertEqual([e.identity for e in world.outgoing(self.a)], [0, 1])
        self.assertEqual([e.identity for e in world.incoming(self.a)], [])
        self.assertEqual([e.identity for e in world.incoming(self.b)], [0, 1, 2])
        self.assertEqual([e.identity for e in world.outgoing(self.b)], [2])

    def test_remove_rejects_dangling_links_without_changing_snapshot(self):
        linked = self.world.connect(self.a, self.b, 5)
        with self.assertRaisesRegex(SpatialError, 'Disconnect'):
            linked.remove(self.b)
        edge = linked.get_link(0)
        cleaned = linked.disconnect(edge).remove(self.b)
        self.assertEqual(len(cleaned.entities), 1)
        self.assertEqual(len(linked.entities), 2)
        with self.assertRaises(SpatialError): cleaned.connect(self.a, self.b, 7)
        relinked = linked.disconnect(edge).connect(self.a, self.b, 7)
        self.assertEqual(relinked.links[0].identity, 1)
        with self.assertRaises(SpatialError): relinked.current_link(edge)

    def test_foreign_endpoints_and_link_references_are_rejected(self):
        other = SpaceValue('other', self.frame, 'str', 'int').insert(self.point(1.0), 'other')
        edge = other.connect(other.get(0), other.get(0), 1).get_link(0)
        with self.assertRaises(SpatialError): self.world.connect(self.a, other.get(0), 2)
        with self.assertRaises(SpatialError): self.world.disconnect(edge)
        with self.assertRaises(SpatialError): self.world.link_source(edge)
