from copy import deepcopy
import unittest

from pixellang.flow_debug import inspect_flows
from pixellang.model import PixelError
from pixellang.typesys import ERROR_FIELDS
from pixellang.vm import VM


def program():
    def instruction(op, arg, node):
        return dict(op=op, arg=arg, node=node, module=0)

    graph = dict(module=0, node=5, name='Pipeline', returnNode=6, stateful=False,
                 inputs=[dict(name='value', node=1, slot=0, type='int')],initialInputs=[],
                 values=[dict(node=2,name='pass',expression=3,slot=1,type='int')],
                 states=[],updates=[],children=[],order=[2],
                 dependencies=[dict(source=1,target=2,reference=3),dict(source=2,target=6,reference=4)],
                 output=dict(node=4,slot=2,type='int'))
    return dict(magic='PIXELVM', vm_version='0.9', language_version='0.9', compiler_version='test',
                records={'Error': dict(ERROR_FIELDS)}, enums={}, effectFunctions=['main'],
                flows={'flow': graph}, constants=[dict(type='int', value=3)], entry=['main'], functions={
                    'main': dict(params=[], locals=0, result='unit', instructions=[
                        instruction('PUSH', 0, 10), instruction('CALL', 'flow', 10),
                        instruction('PRINT', None, 10), instruction('PUSH', 0, 10),
                        instruction('RETURN', None, 10)]),
                    'flow': dict(params=['int'], locals=3, result='int', instructions=[
                        instruction('LOAD', 0, 3), instruction('CALL', 'helper', 2),
                        instruction('STORE', 1, 2), instruction('LOAD', 1, 4),
                        instruction('STORE', 2, 4), instruction('LOAD', 2, 4), instruction('RETURN', None, 6)]),
                    'helper': dict(params=['int'], locals=1, result='int', instructions=[
                        instruction('LOAD', 0, 7), instruction('RETURN', None, 7)]),
                })


class NativeFlowDebugTests(unittest.TestCase):
    def test_input_readiness_expression_phases_and_read_only_inspection(self):
        vm = VM(program())
        self.assertEqual(inspect_flows(vm), [])
        vm.step(snapshot=False)
        vm.step(snapshot=False)
        before = vm.checkpoint()
        flow = inspect_flows(vm)[0]
        self.assertEqual(flow['inputs']['value']['value'], 3)
        self.assertEqual(flow['values'][0]['phase'], 'pending')
        self.assertFalse(flow['output']['ready'])
        self.assertEqual(vm.checkpoint(), before)
        vm.step(snapshot=False)
        vm.step(snapshot=False)
        self.assertEqual(inspect_flows(vm)[0]['values'][0]['phase'], 'running')
        for _ in range(5): vm.step(snapshot=False)
        complete = inspect_flows(vm)[0]
        self.assertEqual(complete['values'][0]['phase'], 'complete')
        self.assertEqual(complete['output']['value'], 3)
        vm.restore_checkpoint(before)
        self.assertEqual(inspect_flows(vm)[0], flow)
        self.assertEqual(vm.run(), [3])

    def test_nested_graph_path_uses_instance_node_identity(self):
        from tests.test_expression_flow_bytecode import program as nested
        vm=VM(nested())
        while vm.call_stack[-1].function!='child':vm.step(snapshot=False)
        frames=inspect_flows(vm)
        self.assertEqual([frame['path'] for frame in frames],[[],[20]])
        self.assertEqual([frame['name'] for frame in frames],['Parent','Child'])
        self.assertEqual(vm.run(),[-1,0,-1,5])

    def test_failure_is_attached_to_the_active_graph_node(self):
        code = program()
        code['functions']['helper']['instructions'] = [{'op': 'TRAP', 'arg': 'failed'}]
        vm = VM(code)
        with self.assertRaises(PixelError): vm.run()
        self.assertEqual(inspect_flows(vm)[0]['values'][0]['phase'], 'failed')

    def test_spatial_projection_inspection_does_not_allocate_or_consume_budget(self):
        code = program()
        code['coordinateFrames'] = {'@0:0': 'meters'}
        code['constants'][0] = dict(type='float64', value=3.0)
        graph = code['flows']['flow']
        graph['inputs'][0]['type'] = 'Point3<@0:0>'
        graph['values'][0]['type'] = 'float64'
        graph['output']['type'] = 'float64'
        code['functions']['helper'].update(params=['float64'], result='float64')
        code['functions']['flow'].update(params=['Point3<@0:0>'], result='float64')
        code['functions']['flow']['instructions'].insert(1, dict(op='FIELD', arg='z', node=3, module=0))
        main = code['functions']['main']['instructions']
        main[:1] = [{'op': 'PUSH', 'arg': 0}] * 3 + [
            {'op': 'SPATIAL_VALUE', 'arg': {'type': 'Point3<@0:0>', 'fields': ['x', 'y', 'z']}}]
        vm = VM(code)
        for _ in range(5): vm.step(snapshot=False)
        before = vm.checkpoint()
        graph = inspect_flows(vm)[0]
        self.assertEqual(graph['values'][0]['phase'], 'pending')
        self.assertEqual(graph['inputs']['value']['value']['coordinates'], [3.0, 3.0, 3.0])
        self.assertEqual(vm.checkpoint(), before)
        self.assertEqual(vm.run(), [3.0])
