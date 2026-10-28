"""Value blocks compute values; only local loops and nested functions own exits."""
import unittest
from pixellang.vm import VM
from tests.compiler_assertions import CompilerAssertions

class ValueBlockBoundaryTests(CompilerAssertions, unittest.TestCase):
    def output(self, source, expected):
        result = self.compile(source)
        self.assertEqual(result['diagnostics'], [], result)
        vm = VM(result['bytecode'])
        self.assertEqual(vm.run(), expected)
        self.assertEqual(vm.value_accesses.entries, {})

    def test_return_cannot_escape_value_expression_contexts(self):
        sources = ['fn identity[T](value:T)->T=value\n'
         'fn choose()->int{let ignored=identity(match 1 {_=>{return 7}})}\n'
         'effect fn main(){print(choose())}',
         'fn choose[T](left:T,right:T)->T=right\n'
         'effect fn mark()->int{print(1);return 2}\n'
         'effect fn value()->int=choose(mark(),match 1 {_=>{return 7}})\n'
         'effect fn main(){print(value())}',
         'fn choose()->int{let ignored=[match 1 {_=>{return 7}},1]}\neffect fn main(){print(choose())}',
         'fn choose()->int{let ignored=map[string,int]{"a":match 1 {_=>{return 7}}}}\n'
         'effect fn main(){print(choose())}',
         'record Item{value:int}\n'
         'fn choose()->int{let ignored=Item{value:match 1 {_=>{return 7}}}}\n'
         'effect fn main(){print(choose())}',
         'record Item{value:int}\n'
         'fn choose(input:Option[int])->int=(match input {Option.Some(n)=>{return '
         'n},Option.None=>Item{value:9}}).value\n'
         'effect fn main(){print(choose(.Some(7)));print(choose(.None))}',
         'effect fn make()->fn(int)->int{print(1);return fn(value:int)->int=value}\n'
         'effect fn choose()->int=make()(match 1 {_=>{return 7}})\n'
         'effect fn main(){print(choose())}',
         'record Ops{run:fn(int)->int}\n'
         'effect fn make()->Ops{print(1);return Ops{run:fn(value:int)->int=value}}\n'
         'effect fn choose()->int=make().run(match 1 {_=>{return 7}})\n'
         'effect fn main(){print(choose())}',
         'fn sum(a:int,b:int,c:int)->int=a+b+c\n'
         'effect fn mark(value:int)->int{print(value);return value}\n'
         'effect fn choose()->int=sum(mark(1),match 1 {_=>{return 7}},mark(3))\n'
         'effect fn main(){print(choose())}',
         'effect fn mark()->int{print(3);return 3}\n'
         'effect fn choose()->int{let ignored=map[string,int]{(match 1 {_=>{return 7}}):mark()}}\n'
         'effect fn main(){print(choose())}',
         'record Item{value:int}\n'
         'effect fn make()->Item{print(1);return Item{value:2}}\n'
         'effect fn choose()->int{let ignored=make() with {value:match 1 {_=>{return 7}}}}\n'
         'effect fn main(){print(choose())}',
         'fn choose()->int{let ignored=jsonDecode::[int](match 1 {_=>{return 7}})}\n'
         'effect fn main(){print(choose())}']
        for source in sources:
            with self.subTest(source=source):
                result = self.compile(source)
                self.assertTrue(any('Return cannot leave a value block' in d['message'] for d in result['diagnostics']), result)

    def test_value_blocks_do_not_inherit_enclosing_loops(self):
        for control in ('break', 'continue'):
            for expression in ('10 + match 1 {_=>{CONTROL; 1}}',
                               'add(&value,match 1 {_=>{CONTROL; 1}})',
                               'match 1 {_=>{while true{let x=match 2{_=>{CONTROL;1}};break};1}}'):
                with self.subTest(control=control, expression=expression):
                    source = 'fn add(value:inout int,amount:int)->int{value+=amount;return value}\nfn main(){var value=0;while true{let x='+expression.replace('CONTROL',control)+';break}}'
                    result = self.compile(source)
                    self.assertTrue(any('Loop control outside a loop' in d['message'] for d in result['diagnostics']), result)

    def test_statement_returns_and_nested_function_returns_remain_local(self):
        self.output('''fn choose(value:int)->int {match value{_=>{return value+1}}}
fn nested()->int = match 1 {_=>{let callback=fn()->int{return 7};callback()}}
effect fn main(){print(choose(2));print(nested())}''', [3,7])

    def test_computed_call_and_contextual_constructor_remain_values(self):
        self.output('fn make()->fn(int)->int=fn(value:int)->int=value+1\nfn choose()->int=make()(6)\neffect fn main(){print(choose())}', [7])
        self.output('enum Outcome[T]{Value(value:T),Empty}\nfn choose()->Outcome[int]=.Value(7)\neffect fn main(){match choose(){.Value(n)=>{print(n)},.Empty=>{print(0)}}}', [7])

    def test_propagation_preserves_evaluation_order_and_releases_pending_access(self):
        prefix = '''record Item{value:int}
record Ops{run:fn(int)->int}
fn add(value:inout int, amount:int)->int{value+=amount;return value}
effect fn mark(value:int)->int{print(value);return value}
effect fn make()->fn(int)->int{print(1);return fn(x:int)->int=x}
effect fn ops()->Ops{print(1);return Ops{run:fn(x:int)->int=x}}
effect fn item()->Item{print(1);return Item{value:2}}
'''
        cases = [
            ('mark(1) + Option::[int].None?', [1]),
            ('[mark(1), Option::[int].None?, mark(3)]', [1]),
            ('map[string,int]{"key":Option::[int].None?}', []),
            ('map[string,int]{Option::[string].None?:mark(3)}', []),
            ('Item{value:Option::[int].None?}', []),
            ('item() with {value:Option::[int].None?}', [1]),
            ('make()(Option::[int].None?)', [1]),
            ('ops().run(Option::[int].None?)', [1]),
            ('add(&value,match 1{_=>{Option::[int].None?}})', []),
            ('jsonDecode::[int](Option::[string].None?)', []),
        ]
        for expression, events in cases:
            with self.subTest(expression=expression):
                source=prefix+'effect fn choose()->Option[int]{var value=10;let ignored='+expression+';return .Some(0)}\neffect fn main(){print(match choose(){.Some(x)=>x,.None=>7})}'
                self.output(source, events+[7])
