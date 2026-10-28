"""Compile expression flows with the native compiler and execute their bytecode."""
from copy import deepcopy
import unittest

from pixellang.vm import VM, validate_bytecode
from pixellang.model import PixelError
from tests import test_native_ir_values as ir_tests
from tests.native_compiler import compile_native_sources


class NativeExpressionFlowExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.code=compile_native_sources(ir_tests.sources())
    lower=ir_tests.NativeIRValueTests.lower
    run_program=ir_tests.NativeIRValueTests.run_program

    def test_counter_reads_old_state_and_commits_next(self):
        code=self.run_program('''flow Counter() step(delta:int)->int {
 state value=0
 let updated=value+delta
 next value=updated
 output value
}
effect fn main(){let c=Counter();print(c.step(2));print(c.step(3));print(c.step(0))}''',[0,2,5])
        graph=next(iter(code['flows'].values()))
        self.assertEqual(len(graph['values']),1)
        self.assertNotIn('connections',graph)
        self.assertNotIn('nodes',graph)

    def test_forward_values_generic_calls_and_value_captures(self):
        self.run_program('''fn identity[T](x:T)->T=x
flow F() step(x:int)->int {
 let callback=fn()->int=value+1
 let result=callback()
 let value=identity(x)*2
 output result
}
effect fn main(){print(F().step(4))}''',[9])

    def test_sibling_instances_have_independent_declared_identity(self):
        code=self.run_program('''flow Child(initial:int) step(x:int)->int {state value=initial;next value=value+x;output value}
flow Parent() step(x:int)->int {
 instance left=Child(1)
 instance right=Child(10)
 let a=left.step(x)
 let b=right.step(x*2)
 output a+b
}
effect fn main(){let p=Parent();print(p.step(2));print(p.step(3));print(p.step(0))}''',[11,17,26])
        self.assertTrue(any(i['op']=='FLOW_CHILD_STEP' for f in code['functions'].values() for i in f['instructions']))

    def test_mutually_exclusive_steps_and_skipped_children(self):
        self.run_program('''enum Event {Left,Right,Skip}
flow Child(initial:int) step(x:int)->int {state value=initial;next value=value+x;output value}
flow Parent() step(event:Event)->int {
 instance child=Child(10)
 output match event {.Left=>child.step(1),.Right=>child.step(2),.Skip=>-1}
}
effect fn main(){let p=Parent();print(p.step(.Skip));print(p.step(.Left));print(p.step(.Skip));print(p.step(.Right));print(p.step(.Left))}''',[-1,10,-1,11,13])

    def test_parent_failure_after_child_step_rolls_back_whole_activation(self):
        result=self.lower('''flow Child() step(x:int)->int {state value=0;next value=value+x;output value}
flow Parent() step(divisor:int)->int {instance child=Child();let prior=child.step(1);output prior/divisor}
effect fn main(){let p=Parent();print(p.step(0))}''')
        self.assertEqual(result['diagnostics'],[])
        vm=VM(result['bytecode'])
        with self.assertRaises(PixelError):vm.run()
        self.assertIsNone(vm.flow_runtime.transaction)
        self.assertEqual(vm.flow_runtime.instances[0].steps,0)
        self.assertEqual(list(vm.flow_runtime.instances[0].cells.values()),[0])

    def test_nested_step_checkpoint_replays_identically(self):
        result=self.lower('''flow Child() step(x:int)->int {state n=0;next n=n+x;output n}
flow Parent() step(x:int)->int {instance child=Child();let result=child.step(x);output result+1}
effect fn main(){let p=Parent();print(p.step(3));print(p.step(2))}''')
        self.assertEqual(result['diagnostics'],[])
        vm=VM(result['bytecode'])
        while not (vm.flow_runtime.transaction and vm.flow_runtime.transaction.staged):vm.step(snapshot=False)
        checkpoint=vm.checkpoint()
        self.assertEqual(vm.run(),[1,4])
        final=vm.checkpoint()
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(),[1,4])
        self.assertEqual(vm.checkpoint(),final)

    def test_untrusted_child_target_and_state_metadata_are_rejected(self):
        code=self.run_program('''flow Child() step(x:int)->int {state n=0;next n=x;output n}
flow Parent() step(x:int)->int {instance child=Child();output child.step(x)}
effect fn main(){let p=Parent();print(p.step(1))}''',[0])
        for mutation in ('unknown-child','missing-update','ordinary-call','bad-value-order'):
            altered=deepcopy(code)
            parent=next(name for name,g in altered['flows'].items() if g['children'])
            child=altered['flows'][parent]['children'][0]['function']
            instruction=next(i for i in altered['functions'][parent]['instructions'] if i['op']=='FLOW_CHILD_STEP')
            if mutation=='unknown-child':instruction['arg']['node']=999999
            elif mutation=='missing-update':altered['flows'][child]['updates']=[]
            elif mutation=='ordinary-call':instruction.update(op='CALL',arg=child)
            else:altered['flows'][parent]['order']=[999999]
            with self.subTest(mutation=mutation),self.assertRaises(PixelError):validate_bytecode(altered)


if __name__=='__main__':unittest.main()
