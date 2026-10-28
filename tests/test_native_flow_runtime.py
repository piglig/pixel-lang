import unittest

from pixellang.model import PixelError
from pixellang.flow_debug import inspect_flows
from pixellang.typesys import ERROR_FIELDS
from pixellang.vm import VM, validate_bytecode


def delay_program():
    def op(name, arg=None, node=0): return dict(op=name, arg=arg, node=node, module=0)
    graph = dict(module=0, node=10, name='Delay', stateful=True, returnNode=6,
                 inputs=[dict(name='value', node=1, slot=0, type='int')], initialInputs=[],
                 states=[dict(node=2, name='previous', slot=1, type='int', initializer='initial')],
                 updates=[dict(node=3, state=2, expression=4)],
                 children=[], values=[], order=[],
                 dependencies=[dict(source=1,target=3,reference=4),dict(source=2,target=6,reference=5)],
                 output=dict(node=5, slot=2, type='int'))
    return dict(magic='PIXELVM', vm_version='0.9', language_version='0.9', compiler_version='test',
                records={'Error': dict(ERROR_FIELDS)}, enums={}, effectFunctions=['main'], flows={'delay': graph},
                constants=[dict(type='int', value=5), dict(type='int', value=0)], entry=['main'], functions={
                    'main': dict(params=[], locals=1, result='unit', instructions=[
                        op('FLOW_NEW', 'delay'), op('STORE', 0),
                        op('LOAD', 0), op('PUSH', 0), op('FLOW_STEP', 'Flow<fn(int)->(int)>'), op('PRINT'),
                        op('LOAD', 0), op('PUSH', 0), op('FLOW_STEP', 'Flow<fn(int)->(int)>'), op('PRINT'),
                        op('PUSH', 1), op('RETURN')]),
                    'initial': dict(params=[], locals=0, result='int', instructions=[op('PUSH', 1), op('RETURN')]),
                    'delay': dict(params=['int'], locals=3, result='int', instructions=[
                        op('FLOW_READ', 2, 2), op('STORE', 1, 2), op('LOAD', 0, 4), op('FLOW_STAGE', 2, 3),
                        op('LOAD', 1, 5), op('STORE', 2, 5), op('LOAD', 2, 5), op('RETURN', None, 6)]),
                })


class NativeFlowRuntimeTests(unittest.TestCase):
    def test_state_reads_old_snapshot_and_commits_after_return(self):
        vm = VM(delay_program(), trace=True)
        while not (vm.flow_runtime.transaction and vm.flow_runtime.transaction.mode == 'step'):
            vm.step(snapshot=False)
        instance = vm.flow_runtime.instances[0]
        for _ in range(4): vm.step(snapshot=False)
        self.assertEqual(list(instance.cells.values()), [0])
        self.assertEqual(list(vm.flow_runtime.transaction.staged.values()), [5])
        checkpoint = vm.checkpoint()
        view = inspect_flows(vm)[0]
        self.assertEqual(view['instance'], 0)
        self.assertEqual(view['step'], 0)
        self.assertEqual(view['states'][0]['value']['value'], 0)
        self.assertEqual(view['updates'][0]['value'], 5)
        self.assertTrue(view['updates'][0]['staged'])
        self.assertEqual(vm.checkpoint(), checkpoint)
        self.assertEqual(vm.run(), [0, 5])
        final = vm.checkpoint()
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(), [0, 5])
        self.assertEqual(vm.checkpoint(), final)
        self.assertEqual(vm.flow_runtime.instances[0].steps, 2)

    def test_instruction_budget_failure_does_not_commit_staged_values(self):
        vm = VM(delay_program())
        while not (vm.flow_runtime.transaction and vm.flow_runtime.transaction.staged):
            vm.step(snapshot=False)
        vm.max_steps = vm.steps
        with self.assertRaises(PixelError): vm.step(snapshot=False)
        self.assertEqual(list(vm.flow_runtime.instances[0].cells.values()), [0])
        self.assertEqual(vm.flow_runtime.instances[0].steps, 0)
        self.assertIsNone(vm.flow_runtime.transaction)

    def test_untrusted_bytecode_cannot_bypass_instance_or_initializer_boundaries(self):
        for change in ('ordinary-call', 'initializer-call', 'missing-update', 'stateful-flag', 'external-init'):
            code = delay_program()
            if change == 'ordinary-call': code['functions']['main']['instructions'][0] = dict(op='FUNCTION', arg='delay')
            elif change == 'initializer-call': code['functions']['main']['instructions'][0] = dict(op='CALL', arg='initial')
            elif change == 'missing-update': code['flows']['delay']['updates'] = []
            elif change == 'stateful-flag': code['flows']['delay']['stateful'] = False
            else: code['functions']['initial']['instructions'].insert(1, dict(op='PRINT'))
            with self.subTest(change=change), self.assertRaises(PixelError): validate_bytecode(code)

    def test_new_instance_can_fail_without_publishing_partial_initialization(self):
        code = delay_program()
        code['functions']['initial']['instructions'] = [dict(op='PUSH', arg=0), dict(op='PUSH', arg=1),
                                                       dict(op='DIV'), dict(op='RETURN')]
        vm = VM(code)
        with self.assertRaises(PixelError): vm.run()
        self.assertEqual(vm.flow_runtime.instances, {})
        self.assertIsNone(vm.flow_runtime.transaction)

    def test_runtime_rejects_effects_in_untrusted_pure_flow_node(self):
        code = delay_program()
        code['functions']['delay']['instructions'][2:2] = [
            dict(op='LOAD', arg=0, node=4, module=0), dict(op='PRINT', node=4, module=0)]
        vm = VM(code)
        with self.assertRaises(PixelError) as error: vm.run()
        self.assertIn('pure function',str(error.exception))
        self.assertEqual(vm.output, [])
        self.assertEqual(vm.flow_runtime.instances[0].steps, 0)
