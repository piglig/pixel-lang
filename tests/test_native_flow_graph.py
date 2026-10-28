"""Expression-flow topology uses the native binder's declaration identities."""
import json
from pathlib import Path
import unittest

from tests.native_compiler import compile_native_sources
from pixellang.code_image import CodeImage
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "project.pxl" as project
import "bindings.pxl" as bindings
import "flow_graph.pxl" as graph
effect fn main() {
 let bound = bindings.Bind(project.Build(map[string,string]{"main.pxl":input()}, "main.pxl"))
 for module, source in bound.project.modules {
  for declaration in source.declarations {
   if declaration.kind != "flow" {continue}
   let published = graph.Analyze(module, declaration.node, bound)
   var edited = published
   edited.order.append(-1)
   assert(len(edited.order)==len(published.order)+1)
   print(published)
   print(source.syntax.nodes)
   return
  }
 }
 assert(false)
}'''


class NativeFlowGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT/'selfhost'/name).read_text() for name in
                 ('model.pxl','lexer.pxl','parser.pxl','project.pxl','bindings.pxl','flow_graph.pxl')}
        cls.code = CodeImage(compile_native_sources({**files,'main.pxl':DRIVER}))

    def analyze(self, source):
        return VM(self.code, input_text=source, max_steps=20_000_000).run()

    def test_topology_uses_original_nodes_and_stable_ready_order(self):
        source = '''flow Pipeline() step(x:int)->int {
 let total = double + 1
 let double = x * 2
 let unused = x
 output total
}'''
        plan,nodes = self.analyze(source)
        self.assertEqual(plan['diagnostics'],[])
        self.assertEqual([nodes[i]['text'] for i in plan['order']],['double','total','unused'])
        self.assertEqual(set(plan['values']),set(plan['order']))
        self.assertTrue(all(nodes[i]['kind']=='flow_value' for i in plan['order']))
        edge=next(e for e in plan['dependencies'] if nodes[e['source']]['text']=='double')
        self.assertEqual(nodes[edge['target']]['text'],'total')
        reference=nodes[edge['reference']]
        self.assertEqual(source[reference['start']:reference['end']],'double')

    def test_direct_output_has_input_dependency_without_fake_call_node(self):
        plan,nodes=self.analyze('flow F() step(x:int)->int {output x+1}')
        self.assertEqual(plan['diagnostics'],[])
        self.assertEqual(plan['order'],[])
        self.assertEqual(plan['instances'],[])
        self.assertEqual(len(plan['dependencies']),1)
        self.assertEqual(nodes[plan['dependencies'][0]['source']]['kind'],'parameter')
        self.assertEqual(plan['dependencies'][0]['target'],plan['output'])

    def test_feedback_reads_old_state_without_combinational_cycle(self):
        plan,nodes=self.analyze('flow Counter() step(delta:int)->int {state value=0;let sum=value+delta;next value=sum;output value}')
        self.assertEqual(plan['diagnostics'],[])
        self.assertEqual(len(plan['states']),1)
        self.assertEqual(len(plan['updates']),1)
        self.assertEqual([nodes[i]['text'] for i in plan['order']],['sum'])
        self.assertTrue(any(e['source']==plan['states'][0] and e['target']==plan['output'] for e in plan['dependencies']))

    def test_lambda_shadowing_and_pattern_bindings_do_not_create_false_cycles(self):
        source='''enum E {Value(value:int)}
flow F() step(x:int)->int {
 let a = fn(a:int)->int = a
 let b = match E.Value(x) {.Value(b)=>b}
 output a(b)
}'''
        plan,nodes=self.analyze(source)
        self.assertEqual(plan['diagnostics'],[])
        self.assertEqual([nodes[i]['text'] for i in plan['order']],['a','b'])
        self.assertFalse(any(e['source']==e['target'] for e in plan['dependencies']))

    def test_value_captures_and_record_shorthand_contribute_dependencies(self):
        source='''record R {value:int}
flow F() step(x:int)->int {
 let callback=fn()->int=value
 let result=R{value}
 let value=x
 output callback()+result.value
}'''
        plan,nodes=self.analyze(source)
        self.assertEqual(plan['diagnostics'],[])
        self.assertEqual([nodes[i]['text'] for i in plan['order']],['value','callback','result'])
        pairs={(nodes[e['source']]['text'],nodes[e['target']]['text']) for e in plan['dependencies']}
        self.assertIn(('value','callback'),pairs)
        self.assertIn(('value','result'),pairs)

    def test_instance_identity_is_separate_from_per_step_values(self):
        source='''flow Parent(initial:int) step(x:int)->int {
 instance child=Counter(initial)
 let result=child.step(x)
 output result
}
flow Counter(initial:int) step(x:int)->int {state n=initial;next n=n+x;output n}'''
        plan,nodes=self.analyze(source)
        self.assertEqual(plan['diagnostics'],[])
        self.assertEqual([nodes[i]['text'] for i in plan['instances']],['child'])
        self.assertEqual([nodes[i]['text'] for i in plan['order']],['result'])
        self.assertTrue(any(e['source']==plan['instances'][0] and nodes[e['target']]['text']=='result' for e in plan['dependencies']))

    def test_creation_scope_cannot_observe_step_inputs_or_state(self):
        for body in ['state s=x;next s=s;output s',
                     'state s=0;state t=s;next s=s;next t=t;output t',
                     'instance c=Counter(x);output x']:
            with self.subTest(body=body):
                plan,_=self.analyze('flow F(initial:int) step(x:int)->int {'+body+'}\nflow Counter(n:int) step(x:int)->int {output x}')
                self.assertTrue(any('Unknown name' in e['message'] and e['phase']=='bind' for e in plan['diagnostics']))

    def test_creation_parameters_are_readonly_step_dependencies(self):
        plan,nodes=self.analyze('flow F(initial:int) step(x:int)->int {output initial}')
        self.assertEqual(plan['diagnostics'], [])
        edge = next(e for e in plan['dependencies'] if e['target'] == plan['output'])
        self.assertEqual(nodes[edge['source']]['kind'], 'initial_parameter')
        self.assertEqual(nodes[edge['source']]['text'], 'initial')

    def test_omitted_next_retains_state_without_synthetic_update(self):
        plan,nodes=self.analyze('flow F() step(x:int)->int {state retained=7;state changed=0;next changed=x;output retained+changed}')
        self.assertEqual(plan['diagnostics'],[])
        self.assertEqual(len(plan['states']),2)
        self.assertEqual([nodes[i]['text'] for i in plan['updates']],['changed'])

    def test_invalid_dependencies_and_state_updates_are_diagnosed(self):
        cases=[('let a=a+1;output a','Combinational cycle'),
               ('let a=b;let b=a;output b','Combinational cycle'),
               ('output missing','Unknown name'),
               ('state s=0;next s=x;next s=x;output s','Multiple next'),
               ('next x=x;output x','declared state'),
               ('let x=1;output x','Duplicate local name'),
               ('output x;output x','exactly one output'),
               ('let a=x','exactly one output')]
        for body,message in cases:
            with self.subTest(body=body):
                plan,_=self.analyze('flow F() step(x:int)->int {'+body+'}')
                self.assertTrue(any(message in e['message'] for e in plan['diagnostics']),plan)
                self.assertTrue(all(e['source']=='main.pxl' for e in plan['diagnostics']))


if __name__=='__main__': unittest.main()
