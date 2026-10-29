"""Data-only native checkpoint values preserve types, identity and sharing."""
from copy import deepcopy
import json
import unittest

from pixellang.native_state import NativeStateCodec, NativeStateLimit
from pixellang.model import PixelError
from pixellang.spatial_values import CoordinateFrame, SpatialValue, FrozenValue, SpaceValue, Transform
from pixellang.vm import VM
from tests.test_native_spatial_vm import program


class NativeStateTests(unittest.TestCase):
    def setUp(self):
        self.vm = VM(program())
        self.vm.space_identities.add('world')
        self.frame = CoordinateFrame('@0:0', 'meters')
        self.point = SpatialValue('Point3', self.frame, (1.0, 2.0, 3.0))

    def test_space_links_transform_and_shared_payload_round_trip(self):
        payload = FrozenValue('int[]', (1, 2, 3))
        scene = SpaceValue('world', self.frame, 'int[]', 'int').insert("a", self.point, payload).insert("b", self.point, payload)
        scene = scene.connect("edge", scene.entity_id("a"), scene.entity_id("b"), 9)
        transform = Transform(self.frame, self.frame,
                              ((1.0,0.0,0.0),(0.0,1.0,0.0),(0.0,0.0,1.0)), (0.0,0.0,0.0))
        encoded = NativeStateCodec(self.vm).encode((scene, scene, transform))
        restored = NativeStateCodec(self.vm).decode(json.loads(json.dumps(encoded, sort_keys=True)))
        self.assertEqual(restored, (scene, scene, transform))
        self.assertIs(restored[0], restored[1])
        self.assertIs(restored[0].require(restored[0].entity_id("a")).payload, restored[0].require(restored[0].entity_id("b")).payload)
        self.assertEqual(restored[0].require_link(restored[0].link_id("edge")).payload, 9)

    def test_business_identities_round_trip_without_snapshot_membership(self):
        scene = SpaceValue('world', self.frame, 'int[]', 'int')
        entity, link = scene.entity_id('order-42'), scene.link_id('route')
        value = (scene, entity, link)
        encoded = NativeStateCodec(self.vm).encode(value)
        restored = NativeStateCodec(self.vm).decode(json.loads(json.dumps(encoded)))
        self.assertEqual(restored, value)
        self.assertIsNone(restored[0].get(restored[1]))
        self.assertIsNone(restored[0].get_link(restored[2]))
        for identity in (entity, link):
            for field, replacement in [('owner', 'foreign'), ('key', ''), ('key', 1)]:
                invalid = NativeStateCodec(self.vm).encode(identity)
                invalid['fields'][field] = replacement
                with self.subTest(field=field), self.assertRaises(PixelError):
                    NativeStateCodec(self.vm).decode(invalid)

    def test_unknown_tags_forward_references_cycles_and_wrong_frames_are_rejected(self):
        values = [{'id':0,'native':'Frame','fields':{}}, {'ref':0}, {'id':0,'tuple':[{'ref':0}]}]
        frame = NativeStateCodec(self.vm).encode(self.frame)
        frame['fields']['unit'] = 'centimeters'
        values.append(frame)
        for value in values:
            with self.subTest(value=value), self.assertRaises(PixelError):
                NativeStateCodec(self.vm).decode(value)

    def test_frozen_container_nominal_types_and_scalar_ranges_are_checked(self):
        invalid = NativeStateCodec(self.vm).encode(FrozenValue('int[]', (True,)))
        with self.assertRaises(PixelError): NativeStateCodec(self.vm).decode(invalid)
        for value in (1 << 63, -(1 << 63) - 1, {'float':float('inf')}):
            with self.assertRaises(PixelError): NativeStateCodec(self.vm).decode(value)
        with self.assertRaises(PixelError): NativeStateCodec(self.vm).encode(self.vm.call_stack[0])
        malformed = NativeStateCodec(self.vm).encode(FrozenValue('str[]', 'abc'))
        with self.assertRaises(PixelError): NativeStateCodec(self.vm).decode(malformed)

    def test_shared_graph_encoding_does_not_expand_every_reference(self):
        payload = FrozenValue('int[]', tuple(range(1000)))
        value = tuple(payload for _ in range(1000))
        document = NativeStateCodec(self.vm, max_bytes=256_000).encode(value)
        self.assertLess(len(json.dumps(document)), 30_000)
        restored = NativeStateCodec(self.vm).decode(document)
        self.assertIs(restored[0], restored[-1])
        with self.assertRaises(NativeStateLimit): NativeStateCodec(self.vm, max_bytes=1).encode(value)
