"""Native multi-subject matching has one representation through execution and PNG."""
from tests.native_compiler import native_service
from copy import deepcopy
import unittest

from pixellang.native_editor import NativeSymbolIndex
from pixellang.vm import VM

SOURCE = '''enum State { Pending, Paid(amount:Option[int]) }
enum Event { Pay, Cancel }
fn decide(state:State,event:Event)->int=match (state,event) {
 (.Pending,.Pay)=>1,
 (.Pending,.Cancel)=>2,
 (.Paid(.Some(amount)),.Pay) if amount>0=>amount,
 (.Paid(.Some(amount)),.Pay)=>-amount,
 (.Paid(.None),.Pay)=>3,
 (.Paid(_),.Cancel)=>4
}
effect fn main(){
 print(decide(.Pending,.Pay))
 print(decide(.Pending,.Cancel))
 print(decide(.Paid(Option.Some(5)),.Pay))
 print(decide(.Paid(Option.Some(-6)),.Pay))
 print(decide(.Paid(Option.None),.Pay))
 print(decide(.Paid(Option.Some(8)),.Cancel))
}
'''


class NativeProductPatternTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)

    def request(self, operation, **args):
        result = self.service.request(dict(version=1, operation=operation, **args))
        self.assertEqual(result['status'], 'ok', result)
        return result['result']

    def run_source(self, source):
        return VM(self.request('compile', files={'main.pxl': source})['bytecode']).run()

    def test_product_nested_payloads_and_guarded_fallback_execute(self):
        self.assertEqual(self.run_source(SOURCE), [1, 2, 5, 6, 3, 4])

    def test_single_subject_grouping_keeps_ordinary_postfix_expressions(self):
        source = """enum S {A,B}
record Box {value:S}
fn read(box:Box)->int=match (box).value {.A=>1,.B=>2}
effect fn main(){print(read(Box{value:.A}))}
"""
        self.assertEqual(self.run_source(source),[1])

    def test_subjects_evaluate_once_and_guards_short_circuit_in_source_order(self):
        source = '''enum S {A(value:int),B}
effect fn subject(n:int)->S {print(n);return .A(n)}
effect fn guard(n:int)->bool {print(n);return false}
effect fn main(){
 let result=match (subject(1),subject(2)) {
  (.B,_) if guard(90)=>90,
  (.A(a),.A(b)) if guard(a+b)=>0,
  (.A(a),.A(b))=>a*10+b,
  (_,_)=>99
 }
 print(result)
}
'''
        self.assertEqual(self.run_source(source), [1, 2, 3, 12])

    def test_statement_patterns_bind_readonly_captures_and_return(self):
        source = '''enum S {A(value:Option[int]),B}
fn read(s:S,delta:int)->int {
 match (s,delta) {
  (.A(.Some(n)),d) if n>d=>{let f=fn()->int=n+d;return f()},
  (.A(.Some(n)),_)=>{return n},
  (.A(.None),_)=>{return 0},
  (.B,d)=>{return d}
 }
}
effect fn main(){print(read(.A(Option.Some(8)),2));print(read(.A(Option.Some(1)),3));print(read(.A(Option.None),4));print(read(.B,5))}
'''
        self.assertEqual(self.run_source(source), [10, 1, 0, 5])

    def test_source_pixels_png_and_recovery_preserve_branch_semantics(self):
        files = {'main.pxl': SOURCE}
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), [1, 2, 5, 6, 3, 4])
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), [1, 2, 5, 6, 3, 4])
        document = deepcopy(self.request('pixels', files=files)['document'])
        document['metadata'] = {}
        for module in document.get('bundle', {}).get('modules', {}).values():
            module['metadata'] = {}
        self.assertEqual(VM(self.request('compile-pixels', document=document)['bytecode']).run(), [1, 2, 5, 6, 3, 4])

    def test_statement_patterns_also_survive_png(self):
        source = '''enum S {A(value:Option[int]),B}
effect fn main(){
 let s:S=.A(Option.Some(7))
 match (s,3) {
  (.A(.Some(n)),d) if n>d=>{print(n+d)},
  (_,_)=>{print(0)}
 }
}'''
        image=self.request('export-png', files={'main.pxl':source})['image']
        restored=self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(),[10])
        self.assertEqual(VM(self.request('compile', **restored)['bytecode']).run(),[10])

    def test_imported_generic_qualified_patterns(self):
        files = {'data.pxl': 'export enum Box[T] {Empty,Value(item:T)}',
                 'main.pxl': '''import "data.pxl" as d
fn read(box:d.Box[Option[int]])->int=match box {
 d.Box[Option[int]].Value(Option[int].Some(n))=>n,
 d.Box.Value(.None)=>0,
 d.Box.Empty=>-1
}
effect fn main(){print(read(d.Box::[Option[int]].Value(Option.Some(9))));print(read(d.Box::[Option[int]].Empty))}'''}
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), [9, -1])

    def test_child_steps_in_guards_follow_selected_branch_paths(self):
        source = """enum S {A,B}
flow Child() step()->bool {state count=0;next count=count+1;output true}
flow Parent() step(s:S)->bool {
 instance child=Child()
 output match s {.A=>child.step(),.B if child.step()=>true,.B=>false}
}
effect fn main(){let p=Parent();print(p.step(.A));print(p.step(.B))}
"""
        self.assertEqual(self.run_source(source),[True,True])
        invalid=source.replace('.A=>child.step(),.B if child.step()=>true,.B=>false',
                               '.A if child.step()=>true,_=>child.step()')
        response=self.service.request(dict(version=1,operation='compile',files={'main.pxl':invalid}))
        self.assertEqual(response['status'],'diagnostics',response)
        self.assertIn('more than once',str(response))

    def test_formatter_preserves_multi_subject_match_and_guards(self):
        first=self.request('format', files={'main.pxl':SOURCE})['text']
        second=self.request('format', files={'main.pxl':first})['text']
        self.assertEqual(first,second)
        self.assertEqual(self.run_source(first),[1,2,5,6,3,4])

    def test_guard_completion_includes_pattern_bindings(self):
        files={'main.pxl':SOURCE}
        def analyze(sources):
            native=self.request('editor-index',files=sources)['index']
            return NativeSymbolIndex(sources,'main.pxl',native)
        index=analyze(files)
        items=index.complete('main.pxl',SOURCE.index('amount>0')+3,analyze=analyze)
        self.assertIn('amount',[item['label'] for item in items])

    def test_editor_resolves_each_nested_pattern_to_its_own_enum(self):
        source='''enum Inner {Value(n:int),Empty}
enum Outer {Value(inner:Inner),Empty}
fn read(x:Outer)->int=match x {.Value(.Value(n))=>n,.Value(.Empty)=>0,.Empty=>-1}
fn main(){}'''
        files={'main.pxl':source}
        def analyze(sources):
            native=self.request('editor-index',files=sources)['index']
            result=NativeSymbolIndex(sources,'main.pxl',native)
            self.assertEqual(result.diagnostics,[])
            return result
        index=analyze(files)
        outer=source.index('.Value(.Value')+1
        inner=source.index('.Value(n)')+1
        self.assertEqual(index.definition('main.pxl',outer)['start']['offset'],source.index('Value(inner'))
        self.assertEqual(index.definition('main.pxl',inner)['start']['offset'],source.index('Value(n:int)'))
        edits=index.rename('main.pxl',inner,'Item',analyze=analyze)
        self.assertEqual(len(edits),2)


if __name__=='__main__':unittest.main()
