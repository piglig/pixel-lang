"""Expression flows use ordinary native type/effect checking and child identities."""
import json
from pathlib import Path
import re
import unittest

from tests.native_compiler import compile_native_sources
from pixellang.code_image import CodeImage
from pixellang.vm import VM

ROOT=Path(__file__).resolve().parents[1]
DRIVER='''import "model.pxl" as model
import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
record Output {diagnostics:[model.Diagnostic],flows:[model.TypedFlow],calls:[model.CallResolution],symbols:[model.Symbol]}
effect fn RunTest()->Result[int,Error] {
 let files=(jsonDecode::[map[string,string]](input()))?
 let checked=checker.Check(types.Analyze(bindings.Bind(project.Build(files,"main.pxl"))))
 print(Output{diagnostics:checked.diagnostics,flows:checked.flows,calls:checked.calls,symbols:checked.resolved.bound.symbols})

 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
'''


class NativeFlowTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files={'main.pxl':DRIVER}
        pending=re.findall(r'import "([^"]+)"',DRIVER)
        while pending:
            name=pending.pop()
            if name in files:continue
            files[name]=(ROOT/'selfhost'/name).read_text()
            pending.extend(re.findall(r'import "([^"]+)"',files[name]))
        cls.code=CodeImage(compile_native_sources(files))

    def analyze(self,source,**modules):
        return VM(self.code,input_text=json.dumps({'main.pxl':source,**modules}),
                  max_steps=40_000_000,max_heap_items=32_000_000).run()[0]

    def assertRejected(self,source,message,**modules):
        result=self.analyze(source,**modules)
        self.assertTrue(any(message in d['message'] for d in result['diagnostics']),result)

    def test_forward_values_generic_calls_and_record_updates(self):
        result=self.analyze('''record Data {value:int}
fn identity[T](x:T)->T=x
flow F() step(x:int)->Data {
 let result=original with {value:identity(x)+1}
 let original=Data{value:x}
 output result
}''')
        self.assertEqual(result['diagnostics'],[])
        self.assertTrue(all(s['typeId']>=0 for s in result['symbols'] if s['kind']=='flow_value'))

    def test_inferred_and_annotated_state_types_and_contextual_variants(self):
        result=self.analyze('''enum S {Waiting,Paid(amount:int)}
flow F() step(event:S)->S {
 state status:S=.Waiting
 state count=0
 let nextCount=count+1
 next count=nextCount
 next status=event
 output status
}''')
        self.assertEqual(result['diagnostics'],[])
        self.assertEqual(len(result['flows'][0]['initializers']),2)
        self.assertTrue(all(s['typeId']>=0 for s in result['symbols'] if s['kind']=='flow_state'))

    def test_state_and_next_and_output_mismatches(self):
        for body in ['state s:int="wrong";next s=x;output s',
                     'state s=0;next s="wrong";output s',
                     'let y:string=x;output x',
                     'output "wrong"']:
            with self.subTest(body=body):
                self.assertRejected('flow F() step(x:int)->int {'+body+'}','Type mismatch')

    def test_state_reads_do_not_grant_mutation_access(self):
        self.assertRejected('flow F() step(x:int)->int {state values=[1];let ignored=values.append(x);next values=values;output x}', 'readonly')

    def test_imported_pure_and_effectful_calls_keep_their_contracts(self):
        source='import "lib.pxl" as lib\nflow F() step(x:int)->int {output lib.work(x)}'
        self.assertEqual(self.analyze(source,**{'lib.pxl':'export fn work(x:int)->int=x'})['diagnostics'],[])
        self.assertRejected(source,'effect',**{'lib.pxl':'export effect fn work(x:int)->int=x'})
        self.assertRejected(source,'private',**{'lib.pxl':'fn work(x:int)->int=x'})

    def test_child_creation_and_step_keep_distinct_resolutions(self):
        result=self.analyze('''flow Parent(initial:int) step(x:int)->int {
 instance child=Counter(initial)
 let result=child.step(x)
 output result
}
flow Counter(initial:int) step(delta:int)->int {state value=initial;next value=value+delta;output value}''')
        self.assertEqual(result['diagnostics'],[])
        self.assertEqual(sum(c['kind']=='flow-instance-new' for c in result['calls']),1)
        self.assertEqual(sum(c['kind']=='flow-child-step' for c in result['calls']),1)
        self.assertTrue(all(f['stateful'] for f in result['flows']))

    def test_child_constructor_and_step_argument_errors(self):
        suffix='\nflow Counter(initial:int) step(delta:int)->int {state value=initial;next value=delta;output value}'
        for constructor,output,message in [('Counter()','child.step(x)','constructor argument count'),
                                           ('Counter("bad")','child.step(x)','Type mismatch'),
                                           ('Counter(0)','child.step()','input count'),
                                           ('Counter(0)','child.step("bad")','Type mismatch')]:
            with self.subTest(constructor=constructor,output=output):
                self.assertRejected('flow Parent() step(x:int)->int {instance child='+constructor+';output '+output+'}'+suffix,message)

    def test_child_cannot_escape_or_step_from_callback(self):
        suffix='\nflow Counter() step(x:int)->int {state n=0;next n=x;output n}'
        for body,message in [('let alias=child;output x','cannot escape'),
                             ('let callback=fn()->int=child.step(x);output callback()','owning flow'),
                             ('let callback=child.step;output x','cannot escape')]:
            with self.subTest(body=body):
                self.assertRejected('flow Parent() step(x:int)->int {instance child=Counter();'+body+'}'+suffix,message)

    def test_only_static_flow_declarations_can_create_children(self):
        for creation,suffix,message in [('Pass(0)','fn Pass(x:int)->int=x','must name a flow'),
                                        ('Counter','flow Counter() step(x:int)->int {output x}','direct flow constructor')]:
            self.assertRejected('flow Parent() step(x:int)->int {instance child='+creation+';output x}\n'+suffix,message)

    def test_recursive_children_and_direct_stateful_calls_are_rejected(self):
        self.assertRejected('flow F() step(x:int)->int {instance child=F();output child.step(x)}','recursive instance')
        self.assertRejected('flow F() step(x:int)->int {state n=0;next n=x;output n}\nfn call(){let instance=F()}','effect')

    def test_child_steps_are_counted_per_activation_path(self):
        suffix='\nflow Counter() step(x:int)->int {state n=0;next n=x;output n}'
        for body in ['let a=child.step(x);output child.step(x)',
                     'output child.step(child.step(x))',
                     'state n=0;next n=child.step(x);output child.step(x)']:
            self.assertRejected('flow Parent() step(x:int)->int {instance child=Counter();'+body+'}'+suffix,'more than once')
        result=self.analyze('enum Choice {Left,Right}\nflow Parent() step(choice:Choice,x:int)->int {instance child=Counter();output match choice {.Left=>child.step(x),.Right=>child.step(x+1)}}'+suffix)
        self.assertEqual(result['diagnostics'],[])
        result=self.analyze('flow Parent() step(x:int)->int {instance child=Counter();let value=child.step(x);output value+value}'+suffix)
        self.assertEqual(result['diagnostics'],[])


if __name__=='__main__':unittest.main()
