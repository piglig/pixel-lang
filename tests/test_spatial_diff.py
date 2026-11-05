"""Snapshot differences agree with full comparisons while skipping shared pages."""
import random
import unittest
from dataclasses import replace
from pixellang.spatial_values import CoordinateFrame, SpatialValue, SpatialOrigin, SpaceValue, SpatialError
from pixellang.spatial_diff import compare_spaces


class SpatialDiffTests(unittest.TestCase):
    def setUp(self):
        self.frame = CoordinateFrame('World', 'meters')
        self.point = SpatialValue('Point3', self.frame, (0., 0., 0.))
        self.empty = SpaceValue('world', self.frame, 'int', 'int')

    def test_identity_and_data_are_independent_of_snapshot_versions_and_origins(self):
        original = self.empty.insert('order', self.point, 1)
        left = original.set_payload(original.entity_id('order'), 2)
        right = original.set_payload(original.entity_id('order'), 3)
        self.assertEqual(left.version, right.version)
        change, = compare_spaces(left, right).entities
        self.assertEqual(change.fields, ('payload',))
        self.assertEqual((change.before.payload, change.after.payload), (2, 3))
        moved = original.move(original.entity_id('order'), replace(self.point, origin=SpatialOrigin('source', 9)))
        self.assertTrue(compare_spaces(original, moved).empty)
        reverted = left.set_payload(left.entity_id('order'), 1)
        self.assertTrue(compare_spaces(original, reverted).empty)
        with self.assertRaises(SpatialError):
            compare_spaces(original, replace(original, identity='another'))
        with self.assertRaises(SpatialError):
            compare_spaces(original, replace(original, link_type='str'))

    def test_add_remove_move_and_recreated_link_endpoints(self):
        original = self.empty.insert('a', self.point, 1).insert('b', self.point, 2)
        original = original.connect('route', original.entity_id('a'), original.entity_id('b'), 5)
        changed = original.disconnect(original.link_id('route'))
        changed = changed.connect('route', changed.entity_id('b'), changed.entity_id('a'), 5)
        changed = changed.move(changed.entity_id('a'), replace(self.point, xyz=(1., 0., 2.)))
        diff = compare_spaces(original, changed)
        self.assertEqual(diff.entities[0].fields, ('position',))
        self.assertEqual(diff.links[0].fields, ('source', 'target'))
        removed = changed.remove(changed.entity_id('a'), cascade=True)
        self.assertEqual([x.kind for x in compare_spaces(changed, removed).links], ['removed'])
        self.assertEqual([x.kind for x in compare_spaces(self.empty, original).entities], ['added', 'added'])

    def test_single_edit_skips_unchanged_pages(self):
        original = self.empty
        for index in range(2048):
            original = original.insert(str(index), self.point, index)
        diff = compare_spaces(original, original.set_payload(original.entity_id('1000'), -1))
        self.assertEqual(diff.compared_entries, 1)
        self.assertLessEqual(diff.visited_nodes, 3)
        self.assertEqual(compare_spaces(original, original).visited_nodes, 0)

    def test_random_growth_deletion_compaction_and_reinsertion_match_full_scan(self):
        randomizer = random.Random(73)
        scene = self.empty
        for index in range(1100):
            scene = scene.insert(str(index), self.point, index)
        snapshots = [self.empty, scene]
        for step in range(1000):
            key = str(randomizer.randrange(1100))
            entity = scene.get(scene.entity_id(key))
            if entity is None:
                scene = scene.insert(key, self.point, step)
            elif step % 3:
                scene = scene.remove(entity.id)
            else:
                scene = scene.set_payload(entity.id, -step)
            if step % 50 == 0:
                snapshots.append(scene)
        for before, after in zip(snapshots, snapshots[1:] + [self.empty]):
            old = {e.identity:e for e in before.entities}
            new = {e.identity:e for e in after.entities}
            expected = sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
            self.assertEqual([c.key for c in compare_spaces(before, after).entities], expected)
            self.assertEqual([c.key for c in compare_spaces(after, before).entities], expected)


if __name__ == '__main__':
    unittest.main()
