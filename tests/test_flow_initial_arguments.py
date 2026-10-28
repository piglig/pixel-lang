"""Native constructor execution, independently of surface-language lowering."""
from copy import deepcopy
import unittest

from pixellang.model import PixelError
from pixellang.vm import VM, validate_bytecode
from tests.test_native_flow_runtime import delay_program


def op(name, arg=None):
    return dict(op=name, arg=arg)


def configured_delay(typ='int'):
    code = delay_program()
    graph = code['flows']['delay']
    graph['initialInputs'] = [dict(name='seed', node=11, slot=0, type=typ)]
    for item in (graph['inputs'][0], graph['states'][0], graph['output']):
        item['type'] = typ
    code['functions']['initial'].update(params=[typ], locals=1, result=typ,
                                        instructions=[op('LOAD', 0), op('RETURN')])
    code['functions']['delay'].update(params=[typ], result=typ)
    return code


def nested_delay():
    code = configured_delay()
    code['flows']['flow'] = dict(module=0,node=10,name='Parent',stateful=True,
        inputs=[dict(name='value',node=1,slot=0,type='int')],
        initialInputs=[dict(name='seed',node=20,slot=0,type='int')],
        states=[],updates=[],values=[],order=[],
        children=[dict(node=2,name='child',function='delay',initializers=['child_seed'])],
        dependencies=[dict(source=2,target=6,reference=4),dict(source=1,target=6,reference=3)],
        returnNode=6,output=dict(node=5,slot=1,type='int'))
    code['functions']['flow'] = dict(params=['int'],locals=2,result='int',instructions=[
        dict(op='LOAD',arg=0,node=3,module=0),
        dict(op='FLOW_CHILD_STEP',arg=dict(node=2,reference=4),node=5,module=0),
        dict(op='STORE',arg=1,node=5,module=0),dict(op='LOAD',arg=1,node=5,module=0),
        dict(op='RETURN',arg=None,node=6,module=0)])
    code['functions']['child_seed'] = dict(params=['int'], locals=1, result='int', instructions=[
        op('LOAD', 0), op('PUSH', 0), op('ADD'), op('RETURN')])
    code['functions']['main'].update(locals=1, instructions=[
        op('PUSH', 0), op('FLOW_NEW', 'flow'), op('STORE', 0),
        op('LOAD', 0), op('PUSH', 1), op('FLOW_STEP', 'Flow<fn(int)->(int)>'), op('PRINT'),
        op('PUSH', 1), op('RETURN')])
    return code


class FlowInitialArgumentTests(unittest.TestCase):
    def test_space_state_initializer_uses_construction_permission_and_replays(self):
        typ = 'Space<@0:0,int,str>'
        code = configured_delay(typ)
        code['coordinateFrames'] = {'@0:0': 'meters'}
        code['flows']['delay']['initialInputs'] = []
        code['constants'].append(dict(type='str', value='world'))
        code['functions']['initial'].update(params=[], locals=0, instructions=[
            op('PUSH', 2), op('SPACE_NEW', typ), op('RETURN')])
        code['functions']['main'].update(locals=2, instructions=[
            op('FLOW_NEW', 'delay'), op('STORE', 0),
            op('FLOW_NEW', 'delay'), op('STORE', 1), op('PUSH', 1), op('RETURN')])
        vm = VM(code)
        while not (vm.call_stack and vm.call_stack[-1].flow_role == 'init'):
            vm.step(snapshot=False)
        checkpoint = vm.checkpoint()
        self.assertEqual(vm.run(), [])
        self.assertEqual(len(vm.space_identities), 2)
        self.assertEqual(len(vm.flow_runtime.instances), 2)
        final = vm.checkpoint()
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(), [])
        self.assertEqual(vm.checkpoint(), final)

    def test_space_allocation_is_not_allowed_in_child_constructor_arguments(self):
        code = nested_delay()
        code['coordinateFrames'] = {'@0:0': 'meters'}
        code['functions']['child_seed']['instructions'].insert(
            0, op('SPACE_NEW', 'Space<@0:0,int,str>'))
        with self.assertRaisesRegex(PixelError, 'nested constructor identity effect'):
            validate_bytecode(code)

    def test_failed_initialization_releases_created_space_identity(self):
        code = delay_program()
        code['coordinateFrames'] = {'@0:0': 'meters'}
        code['constants'].append(dict(type='str', value='world'))
        code['functions']['initial']['instructions'] = [
            op('PUSH', 2), op('SPACE_NEW', 'Space<@0:0,int,str>'), op('POP'),
            op('PUSH', 0), op('PUSH', 1), op('DIV'), op('RETURN')]
        vm = VM(code)
        with self.assertRaises(PixelError):
            vm.run()
        self.assertEqual(vm.flow_runtime.instances, {})
        self.assertIsNone(vm.flow_runtime.transaction)
        self.assertEqual(vm.space_identities, set())
        self.assertEqual(vm.spatial_items, 0)

    def test_constructor_values_are_distinct_from_step_inputs_and_instance_local(self):
        code = configured_delay()
        code['functions']['main'].update(locals=2, instructions=[
            op('PUSH', 0), op('FLOW_NEW', 'delay'), op('STORE', 0),
            op('PUSH', 1), op('FLOW_NEW', 'delay'), op('STORE', 1),
            op('LOAD', 0), op('PUSH', 1), op('FLOW_STEP', 'Flow<fn(int)->(int)>'), op('PRINT'),
            op('LOAD', 1), op('PUSH', 0), op('FLOW_STEP', 'Flow<fn(int)->(int)>'), op('PRINT'),
            op('PUSH', 1), op('RETURN')])
        vm = VM(code)
        self.assertEqual(vm.run(), [5, 0])
        self.assertEqual([instance.cells for instance in vm.flow_runtime.instances.values()],
                         [{((), 2): 0}, {((), 2): 5}])

    def test_initial_arrays_are_detached_from_caller_across_gc(self):
        code = configured_delay('int[]')
        code['functions']['main'].update(locals=2, instructions=[
            op('PUSH', 0), op('ARRAY', dict(type='int[]', count=1)), op('STORE', 0),
            op('LOAD', 0), op('FLOW_NEW', 'delay'), op('STORE', 1),
            op('LOAD', 0), op('PUSH', 1), op('PUSH', 1), op('VALUE_SET', [{'index': True}]), op('STORE', 0),
            op('LOAD', 1), op('LOAD', 0), op('FLOW_STEP', 'Flow<fn(int[])->(int[])>'), op('PRINT'),
            op('LOAD', 0), op('PRINT'), op('PUSH', 1), op('RETURN')])
        vm = VM(code, max_heap_objects=3)
        self.assertEqual(vm.run(), [[5], [0]])
        self.assertGreater(vm.collections, 0)
        self.assertIsNone(vm.flow_runtime.transaction)
        self.assertEqual(vm.readonly_counts, {})

    def test_initializer_inputs_are_readonly_even_in_untrusted_bytecode(self):
        code = configured_delay('int[]')
        code['functions']['main']['instructions'] = [op('PUSH', 0), op('ARRAY', dict(type='int[]', count=1)),
            op('STORE', 0), op('LOAD', 0), op('FLOW_NEW', 'delay'), op('RETURN')]
        code['functions']['initial']['instructions'] = [op('LOAD', 0), op('PUSH', 1), op('PUSH', 1),
            op('INDEX_SET'), op('LOAD', 0), op('RETURN')]
        vm = VM(code)
        with self.assertRaises(PixelError) as error:
            vm.run()
        self.assertEqual(error.exception.code, 'flow.input_mutation')
        caller = vm.call_stack[0].memory[0]
        self.assertEqual(vm.heap[caller.address]['items'], [5])
        self.assertEqual(vm.flow_runtime.instances, {})
        self.assertIsNone(vm.flow_runtime.transaction)
        self.assertEqual(vm.spatial_items, 0)

    def test_nested_constructor_arguments_and_mid_initialization_replay(self):
        vm = VM(nested_delay())
        while not (vm.flow_runtime.transaction and vm.flow_runtime.transaction.cursor == 1):
            vm.step(snapshot=False)
        self.assertEqual(vm.flow_runtime.transaction.initial_arguments, {(): [5], (2,): [10]})
        self.assertEqual(vm.flow_runtime.instances, {})
        checkpoint = vm.checkpoint()
        self.assertEqual(vm.run(), [10])
        completed = vm.checkpoint()
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(), [10])
        self.assertEqual(vm.checkpoint(), completed)

    def test_nested_constructor_failure_rolls_back_native_budget_and_publication(self):
        code = nested_delay()
        code['functions']['child_seed']['instructions'] = [op('LOAD', 0), op('PUSH', 1), op('DIV'), op('RETURN')]
        vm = VM(code)
        with self.assertRaises(PixelError):
            vm.run()
        self.assertEqual(vm.flow_runtime.instances, {})
        self.assertIsNone(vm.flow_runtime.transaction)
        self.assertEqual(vm.spatial_items, 0)
        self.assertEqual(vm.space_identities, set())

    def test_later_child_failure_discards_already_initialized_sibling(self):
        code = nested_delay()
        graph = code['flows']['flow']
        sibling = deepcopy(graph['children'][0])
        sibling.update(node=9, name='second', initializers=['second_seed'])
        graph['children'].append(sibling)
        code['functions']['second_seed'] = dict(params=['int'], locals=1, result='int', instructions=[
            op('LOAD', 0), op('PUSH', 1), op('DIV'), op('RETURN')])
        vm = VM(code)
        while not (vm.flow_runtime.transaction and vm.flow_runtime.transaction.instance.cells):
            vm.step(snapshot=False)
        self.assertEqual(vm.flow_runtime.transaction.instance.cells, {((2,), 2): 10})
        self.assertEqual(vm.flow_runtime.instances, {})
        with self.assertRaises(PixelError):
            vm.run()
        self.assertIsNone(vm.flow_runtime.transaction)
        self.assertEqual(vm.flow_runtime.instances, {})
        self.assertEqual(vm.spatial_items, 0)

    def test_constructor_metadata_cannot_forge_signatures_or_escape_initialization(self):
        base = nested_delay()
        mutations = [
            lambda c: c['flows']['delay']['initialInputs'][0].update(type='@9:9'),
            lambda c: c['flows']['delay']['initialInputs'][0].update(slot=1),
            lambda c: c['flows']['delay']['initialInputs'][0].update(name='not a name'),
            lambda c: c['flows']['delay']['initialInputs'][0].update(node=1),
            lambda c: c['flows']['flow']['children'][0].update(initializers=[]),
            lambda c: c['functions']['child_seed'].update(params=[]),
            lambda c: c['functions']['child_seed'].update(result='bool'),
            lambda c: c['functions']['main']['instructions'].insert(0, op('CALL', 'child_seed')),
            lambda c: c['functions']['child_seed']['instructions'].insert(0, op('PRINT')),
            lambda c: c['flows']['flow']['children'][0].update(initializers=['initial']),
        ]
        for mutate in mutations:
            code = deepcopy(base)
            mutate(code)
            with self.subTest(mutation=mutations.index(mutate)), self.assertRaises(PixelError):
                validate_bytecode(code)

    def test_constructor_and_step_names_have_separate_scopes(self):
        code=nested_delay()
        code['flows']['delay']['initialInputs'][0]['name']='value'
        self.assertEqual(VM(code).run(),[10])

    def test_runtime_checks_root_argument_type_and_count(self):
        for instructions in ([op('FLOW_NEW', 'delay')],
                             [op('PUSH', 0), op('ARRAY', dict(type='int[]', count=1)), op('FLOW_NEW', 'delay')]):
            code = configured_delay()
            code['functions']['main']['instructions'] = instructions
            vm = VM(code)
            with self.subTest(instructions=instructions), self.assertRaises(PixelError):
                vm.run()
            self.assertEqual(vm.flow_runtime.instances, {})
