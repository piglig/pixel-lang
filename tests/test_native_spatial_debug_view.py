"""Native spatial inspection is paged, historical and free of VM side effects."""
from types import SimpleNamespace
import unittest

from pixellang import spatial_values as spatial
from pixellang.debugvalues import inspect_values
from pixellang.model import PixelError
from pixellang.spatial_debug_view import space_view
from pixellang.vm import VM
from pixellang.workstation import Workstation
from tests.test_native_spatial_vm import program


class NativeSpatialDebugViewTests(unittest.TestCase):
    def setUp(self):
        frame = spatial.CoordinateFrame('@0:0', 'meters')
        self.point = lambda z: spatial.SpatialValue('Point3', frame, (0.0, 0.0, z))
        payload = spatial.FrozenValue('int[]', (10, 20, 30))
        before = spatial.SpaceValue('room', frame, 'int[]', 'str').insert("a", self.point(0.0), payload).insert("b", self.point(2.0), payload)
        after = before.move(before.entity_id("b"), self.point(3.0))
        live, replay = VM(program()), VM(program())
        live.stack.append(after)
        replay.stack.append(before)
        self.timeline = SimpleNamespace(cursor=1, events=[{}, {}], vm=live, replay_vm=replay,
            compiled=SimpleNamespace(debug={'types': {'@0:0': 'main.pxl:World'}}),
            source={'metadata': {'filename': 'main.pxl'}}, source_project={'entry': 'main.pxl'})

    def view(self, **args):
        return space_view(self.timeline, dict(z=self.timeline.cursor, path=['stack', 0], **args))

    def test_z_changes_neighbors_but_not_historical_snapshot(self):
        before_live = self.timeline.vm.checkpoint()
        before_replay = self.timeline.replay_vm.checkpoint()
        old = self.view(neighbors=dict(origin="a", radius=2.0))
        self.assertEqual(old['total'], 1)
        self.assertEqual(old['entities'][0]['position'], [0.0, 0.0, 2.0])
        self.assertEqual(old['entities'][0]['distance'], 2.0)
        self.assertEqual((old['time'], old['version'], old['frame'], old['unit']), (1, 2, '@0:0', 'meters'))
        self.timeline.cursor = 2
        self.assertEqual(self.view(neighbors=dict(origin="a", radius=2.0))['total'], 0)
        self.timeline.cursor = 1
        self.assertEqual(self.view(neighbors=dict(origin="a", radius=2.0)), old)
        self.assertEqual(self.timeline.vm.checkpoint(), before_live)
        self.assertEqual(self.timeline.replay_vm.checkpoint(), before_replay)

    def test_paging_preserves_payload_paths_and_entity_identity(self):
        view = self.view(start=1, count=1)
        self.assertEqual(view['total'], 2)
        entity = view['entities'][0]
        self.assertEqual(entity['id'], 'b')
        children = inspect_values(self.timeline, dict(z=1, path=entity['payload']['path'], start=1, count=1))
        self.assertEqual(children['variables'][0]['value'], '20')
        entity_view = space_view(self.timeline, dict(z=1, path=['stack', 0, 'entities', 1]))
        self.assertEqual(entity_view['version'], None)
        self.assertEqual(entity_view['entities'], [dict(entity, distance=None)])
        self.assertEqual(self.view(start=100)['entities'], [])

    def test_workstation_routes_native_space_inspection(self):
        workstation = Workstation()
        self.addCleanup(workstation.compiler.close)
        workstation.sessions['native'] = self.timeline
        view = workstation.request('native-space-view', dict(session='native', z=1,
            path=['stack', 0], neighbors=dict(origin="a", radius=2.0)))
        self.assertEqual(view['entities'][0]['id'], 'b')
        self.assertEqual(view['time'], 1)

    def test_canvas_links_are_bounded_to_visible_entities_and_historical_state(self):
        world = self.timeline.replay_vm.stack[0]
        for i in range(105):
            world = world.connect(str(i), world.entity_id("a"), world.entity_id("b"), str(i))
        self.timeline.replay_vm.stack[0] = world
        checkpoint = self.timeline.replay_vm.checkpoint()
        view = self.view()
        self.assertEqual((view['linkTotal'], view['visibleLinkTotal'], len(view['links'])), (105, 105, 100))
        self.assertEqual(view['links'][0]['payload']['value'], '"0"')
        self.assertEqual(view['links'][99]['id'], '99')
        page = self.view(start=1, count=1)
        self.assertEqual((page['linkTotal'], page['visibleLinkTotal'], page['links']), (105, 0, []))
        self.assertEqual(self.timeline.replay_vm.checkpoint(), checkpoint)
        self.timeline.cursor = 2
        self.assertEqual(self.view()['links'], [])

    def test_invalid_time_type_page_and_neighborhood(self):
        for args in [dict(count=101), dict(count=True), dict(start=-1),
                     dict(neighbors={'origin': True, 'radius': 2.0}),
                     dict(neighbors={'origin': 'missing', 'radius': 2.0}),
                     dict(neighbors={'origin': 'a', 'radius': float('nan')}),
                     dict(neighbors={'origin': 'a', 'radius': 10 ** 400}),
                     dict(neighbors={'origin': 'a', 'radius': -1.0})]:
            with self.subTest(args=args), self.assertRaises(PixelError): self.view(**args)
        with self.assertRaisesRegex(PixelError, 'different time'):
            space_view(self.timeline, dict(z=2, path=['stack', 0]))
        with self.assertRaisesRegex(PixelError, 'native Space or Entity'):
            space_view(self.timeline, dict(z=1, path=['stack', 0, 'version']))
