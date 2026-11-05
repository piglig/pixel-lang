"""Structural sharing, indexed graph updates and restored storage invariants."""
from dataclasses import replace
import json
import random
import unittest
from unittest.mock import patch

from pixellang.model import PixelError
from pixellang.native_state import NativeStateCodec
from pixellang.spatial_values import CoordinateFrame, SpatialValue, SpaceValue, SpatialTable, SpatialError
from pixellang.vm import VM
from tests.test_native_spatial_vm import program


class SpatialStorageTests(unittest.TestCase):
    def setUp(self):
        self.frame = CoordinateFrame('@0:0', 'meters')
        self.point = SpatialValue('Point3', self.frame, (0.0, 0.0, 0.0))
        self.empty = SpaceValue('world', self.frame, 'int', 'int')

    def test_updates_share_unmodified_storage_and_do_not_scan_tables(self):
        scene = self.empty
        for index in range(2048):
            scene = scene.insert(str(index), self.point, index)
        # A point edit must use the key index, without validating/scanning all values.
        with patch.object(SpatialTable, '__iter__', side_effect=AssertionError('full table scan')):
            changed = scene.set_payload(scene.entity_id('1024'), -1)
            self.assertEqual(changed.require(scene.entity_id('1024')).payload, -1)
        self.assertEqual(scene.require(scene.entity_id('1024')).payload, 1024)
        self.assertIs(changed.links, scene.links)
        self.assertIs(changed.entities._data._root, scene.entities._data._root)
        before = scene.entities._data._order._root
        after = changed.entities._data._order._root
        self.assertGreater(sum(a is b for a, b in zip(before, after)), 0)
        self.assertLess(changed.allocated, 200)

    def test_allocation_failure_does_not_publish_an_update(self):
        scene = self.empty.insert('a', self.point, 1)
        vm = VM(program(), max_heap_items=1)
        with self.assertRaisesRegex(PixelError, 'Spatial allocation budget'):
            vm.space_operation('spaceSetPayload', [scene, scene.entity_id('a'), 2], None)
        self.assertEqual(scene.require(scene.entity_id('a')).payload, 1)

    def test_live_budget_counts_shared_storage_once_across_forks(self):
        from pixellang.native_events import NativeApplicationRuntime
        from tests.test_native_events_runtime import application_program
        runtime = NativeApplicationRuntime(VM(application_program(), max_heap_items=100_000))
        scene = self.empty
        for i in range(1024):
            scene = scene.insert(str(i), self.point, i)
        runtime.vm.stack.append(scene)
        runtime.vm.recount_spatial()
        original = runtime.vm.spatial_items
        changed = scene.set_payload(scene.entity_id('500'), -1)
        runtime.vm.stack.append(changed)
        runtime.vm.recount_spatial()
        self.assertGreater(runtime.vm.spatial_items, original)
        self.assertLess(runtime.vm.spatial_items - original, 200)
        runtime.vm.stack.append(changed)
        runtime.vm.recount_spatial()
        shared = runtime.vm.spatial_items
        runtime.vm.stack.pop()
        runtime.vm.recount_spatial()
        self.assertEqual(runtime.vm.spatial_items, shared)
        runtime.vm.stack.pop()
        runtime.vm.recount_spatial()
        self.assertEqual(runtime.vm.spatial_items, original)

    def test_random_graph_edits_match_an_insertion_ordered_reference(self):
        rng = random.Random(2901)
        scene, entities, links = self.empty, {}, {}
        retained = []
        for step in range(500):
            action = rng.randrange(6)
            if action == 0 or not entities:
                key = f'e{step}'
                scene = scene.insert(key, self.point, step)
                entities[key] = step
            elif action == 1:
                key = rng.choice(list(entities))
                scene = scene.set_payload(scene.entity_id(key), step)
                entities[key] = step
            elif action == 2:
                source, target = rng.choices(list(entities), k=2)
                key = f'l{step}'
                scene = scene.connect(key, scene.entity_id(source), scene.entity_id(target), step)
                links[key] = (source, target, step)
            elif action == 3 and links:
                key = rng.choice(list(links))
                scene = scene.set_link_payload(scene.link_id(key), step)
                links[key] = (*links[key][:2], step)
            elif action == 4 and links:
                key = rng.choice(list(links))
                scene = scene.disconnect(scene.link_id(key))
                del links[key]
            else:
                key = rng.choice(list(entities))
                version = scene.version
                scene = scene.remove(scene.entity_id(key), cascade=True)
                self.assertEqual(scene.version, version + 1)
                del entities[key]
                links = {k: v for k, v in links.items() if key not in v[:2]}
            self.assertEqual([(e.identity, e.payload) for e in scene.entities], list(entities.items()))
            self.assertEqual([(l.identity, (l.source, l.target, l.payload)) for l in scene.links], list(links.items()))
            for key in entities:
                self.assertEqual([l.identity for l in scene.outgoing(scene.entity_id(key))],
                                 [k for k, v in links.items() if v[0] == key])
                self.assertEqual([l.identity for l in scene.incoming(scene.entity_id(key))],
                                 [k for k, v in links.items() if v[1] == key])
            if step % 50 == 0:
                retained.append((scene, entities.copy(), links.copy()))
        for snapshot, expected_entities, expected_links in retained:
            self.assertEqual([(e.identity, e.payload) for e in snapshot.entities], list(expected_entities.items()))
            self.assertEqual([(l.identity, (l.source, l.target, l.payload)) for l in snapshot.links], list(expected_links.items()))

    def test_compaction_self_loops_parallel_edges_and_reinsertion(self):
        scene = self.empty.insert('a', self.point, 1).insert('b', self.point, 2)
        for i in range(80):
            scene = scene.connect(str(i), scene.entity_id('a'), scene.entity_id('b'), i)
        scene = scene.connect('loop', scene.entity_id('a'), scene.entity_id('a'), 100)
        old = scene
        for i in range(70):
            scene = scene.disconnect(scene.link_id(str(i)))
        scene = scene.connect('0', scene.entity_id('a'), scene.entity_id('b'), 200)
        self.assertEqual([l.identity for l in scene.outgoing(scene.entity_id('a'))],
                         [str(i) for i in range(70, 80)] + ['loop', '0'])
        with self.assertRaises(SpatialError):
            scene.remove(scene.entity_id('a'))
        removed = scene.remove(scene.entity_id('a'), cascade=True)
        self.assertEqual(len(removed.links), 0)
        self.assertEqual(len(removed.outgoing_index), 0)
        self.assertEqual(len(removed.incoming_index), 0)
        self.assertEqual(len(old.links), 81)
        recreated = removed.insert('a', self.point, 3)
        self.assertEqual([e.identity for e in recreated.entities], ['b', 'a'])

    def test_storage_rejects_mutable_children_and_malformed_restore_indexes(self):
        with self.assertRaises(SpatialError):
            SpatialTable([('a', [])])
        with self.assertRaises(SpatialError):
            SpatialTable([('a', 'one'), ('a', 'two')])
        scene = self.empty.insert('a', self.point, 1).insert('b', self.point, 2)
        scene = scene.connect('edge', scene.entity_id('a'), scene.entity_id('b'), 3)
        vm = VM(program())
        vm.space_identities.add('world')
        for broken in (replace(scene, outgoing_index=SpatialTable()),
                       replace(scene, incoming_index=SpatialTable([('b', SpatialTable([('bad', 'bad')]))])),
                       replace(scene, entities=SpatialTable([('wrong', scene.entities[0])]))):
            document = NativeStateCodec(vm).encode(broken)
            with self.assertRaises(PixelError):
                NativeStateCodec(vm).decode(document)
        restored = NativeStateCodec(vm).decode(json.loads(json.dumps(NativeStateCodec(vm).encode(scene))))
        changed = restored.set_link_payload(restored.link_id('edge'), 42)
        self.assertEqual(changed.outgoing(changed.entity_id('a'))[0].payload, 42)
        self.assertEqual(scene.outgoing(scene.entity_id('a'))[0].payload, 3)
        self.assertEqual(len(changed.remove(changed.entity_id('b'), cascade=True).links), 0)


if __name__ == '__main__':
    unittest.main()
