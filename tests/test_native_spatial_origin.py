"""Spatial provenance is diagnostic metadata, never part of coordinate semantics."""
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest

from pixellang import spatial_values as spatial
from pixellang.spatial_debug_view import space_view
from pixellang.vm import VM
from pixellang.temporal import Timeline
from pixellang.compiler_client import CompilerClient
from tests.test_native_spatial_vm import program


class NativeSpatialOriginTests(unittest.TestCase):
    def point(self):
        code = program()
        code['functions']['main']['instructions'][3]['node'] = 41
        vm = VM(code)
        for _ in range(4): vm.step(snapshot=False)
        return vm, vm.stack[-1]

    def test_constructor_origin_survives_snapshots_without_changing_equality(self):
        vm, point = self.point()
        self.assertEqual(point.origin, spatial.SpatialOrigin('main', 41))
        plain = replace(point, origin=None)
        self.assertEqual(point, plain)
        self.assertEqual(hash(point), hash(plain))
        self.assertEqual(vm.materialize(point, None), vm.materialize(plain, None))
        checkpoint = vm.checkpoint()
        restored = VM(vm.bytecode)
        restored.restore_checkpoint(checkpoint)
        self.assertEqual(restored.stack[-1].origin, point.origin)
        self.assertEqual(vm.snapshot_value(point), vm.snapshot_value(plain))

    def test_entity_insert_move_and_debug_source_mapping(self):
        vm, point = self.point()
        world = spatial.SpaceValue('room', point.frame, 'int', 'str').insert("a", point, 1)
        entity = world.require(world.entity_id("a"))
        self.assertIs(entity.position, point)
        newer = replace(point, xyz=(1.0, 2.0, 7.0), origin=spatial.SpatialOrigin('main', 90))
        moved = world.move(entity.id, newer)
        self.assertEqual(world.require(world.entity_id("a")).position.origin.node, 41)
        self.assertEqual(moved.require(moved.entity_id("a")).position.origin.node, 90)
        vm.stack[:] = [world, moved]
        timeline = SimpleNamespace(cursor=0, events=[], vm=vm, source_project=None,
            compiled=SimpleNamespace(debug={'types':{},'functions':{'main':{'source':'lib.pxl','module':'main'}}}),
            source={'metadata':{'filename':'lib.pxl'}})
        before = vm.checkpoint()
        view = space_view(timeline, dict(z=0,path=['stack',0]))
        self.assertEqual(view['entities'][0]['origin'], dict(source='lib.pxl',node=41))
        self.assertEqual(vm.checkpoint(), before)

    def test_timeline_seek_and_recording_rebuild_constructor_origin(self):
        source = 'frame World = meters\neffect fn main() { print(Point3::[World]{x: 1.0, y: 2.0, z: 3.0}) }'
        with CompilerClient() as compiler:
            compiled = compiler.debug_project({'main.pxl': source})
        timeline = Timeline(compiled.source, compiled=compiled)
        while not timeline.vm.halted:
            timeline.advance()
            if timeline.vm.stack and isinstance(timeline.vm.stack[-1], spatial.SpatialValue):
                break
        self.assertFalse(timeline.vm.halted)
        origin = timeline.vm.stack[-1].origin
        self.assertIsNotNone(origin)
        selected = timeline.cursor
        while not timeline.vm.halted: timeline.advance()
        timeline.seek(selected)
        self.assertEqual(timeline.replay_vm.stack[-1].origin, origin)
        document = json.loads(json.dumps(timeline.document()))
        restored = Timeline.restore(document, compiled=timeline.compiled)
        self.assertEqual(restored.cursor, selected)
        self.assertEqual(restored.replay_vm.stack[-1].origin, origin)

    def test_arithmetic_does_not_claim_direct_constructor_origin(self):
        _, point = self.point()
        vector = spatial.SpatialValue('Vec3', point.frame, (0.0, 0.0, 1.0), spatial.SpatialOrigin('main', 8))
        self.assertIsNone(spatial.add(point, vector).origin)
        self.assertIsNone(spatial.subtract(point, point).origin)
        self.assertIsNone(spatial.scale(vector, 2.0).origin)
        transform = spatial.Transform(point.frame, point.frame,
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), (0.0, 0.0, 0.0))
        self.assertIsNone(transform.apply(point).origin)

    def test_vm_arithmetic_origin_identifies_operation_and_preserves_history(self):
        code = program()
        instructions = code['functions']['main']['instructions']
        point = instructions[:4]
        point[-1]['node'] = 41
        vector = [dict(item) for item in point]
        vector[-1] = dict(op='SPATIAL_VALUE', arg=dict(type='Vec3<@0:0>', fields=['x','y','z']), node=42)
        code['functions']['main']['instructions'] = point + vector + [
            dict(op='SPATIAL_BINARY', arg='+', node=43), dict(op='DUP',arg=None),
            dict(op='PRINT',arg=None),dict(op='RETURN',arg=None)]
        vm = VM(code)
        for _ in range(9):vm.step(snapshot=False)
        value = vm.stack[-1]
        self.assertEqual(value.xyz, (2.0,4.0,6.0))
        self.assertEqual(value.origin,spatial.SpatialOrigin('main',43))
        self.assertEqual(value,replace(value,origin=None))
        checkpoint=vm.checkpoint()
        vm.run()
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.stack[-1].origin,value.origin)
        world=spatial.SpaceValue('room',value.frame,'int','str').insert("a", value,1)
        self.assertEqual(world.require(world.entity_id("a")).position.origin,value.origin)

    def test_vm_transform_origin_identifies_call_instead_of_operand(self):
        _,point=self.point()
        transform=spatial.Transform(point.frame,point.frame,
            ((1.0,0.0,0.0),(0.0,1.0,0.0),(0.0,0.0,1.0)),(0.0,0.0,5.0))
        code=program()
        code['functions']['main']['instructions']=[dict(op='SPACE_OP',arg='transformPoint',node=99),dict(op='RETURN',arg=None)]
        vm=VM(code)
        vm.stack[:]=[transform,point]
        vm.step(snapshot=False)
        self.assertEqual(vm.stack[-1].xyz,(1.0,2.0,8.0))
        self.assertEqual(vm.stack[-1].origin,spatial.SpatialOrigin('main',99))
        self.assertEqual(point.origin,spatial.SpatialOrigin('main',41))
