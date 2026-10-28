"""Enum construction infers ordinary payload values without permission contexts."""
from tests.native_compiler import native_service
import unittest
from pixellang.vm import VM


class NativeEnumConstructorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, source):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        return self.compiler.request(dict(version=1, operation='compile', files=files))

    def executes(self, source, expected):
        result = self.compile(source)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(VM(result['result']['bytecode']).run(), expected)

    def test_construction_and_pattern_copies_isolate_nested_payloads(self):
        self.executes('''record Box { items: [int] }
enum Choice { Empty, Value(box: Box, counts: map[string,int]) }
effect fn main() {
 var values=[1]
 let choice=Choice.Value(Box{items:values},map[string,int]{"n":1})
 values.append(2)
 match choice {Choice.Value(box,counts)=>{
  var edited=box
  var numbers=counts
  edited.items.append(3)
  numbers["n"]=2
  print(edited.items); print(numbers["n"])
 },Choice.Empty=>{}}
 match choice {Choice.Value(box,counts)=>{print(box.items);print(counts["n"])},Choice.Empty=>{}}
 print(values)
}''', [[1,3],2,[1],1,[1,2]])

    def test_generic_expected_type_and_explicit_specialization(self):
        self.executes('''enum Choice[T] { Empty, Value(item:T) }
effect fn main(){
 let inferred:Choice[[int]]=Choice.Value([])
 let explicit=Choice::[[int]].Value([1])
 let empty:Choice[int]=Choice.Empty
 match inferred {Choice.Value(item)=>{print(item)},Choice.Empty=>{}}
 match explicit {Choice.Value(item)=>{print(item)},Choice.Empty=>{}}
 match empty {Choice.Value(item)=>{print(item)},Choice.Empty=>{print(0)}}
}''', [[],[1],0])

    def test_imported_constructor_uses_return_and_argument_contexts(self):
        self.executes({
            'data.pxl':'export enum Choice[T] { Empty, Value(item:T) }',
            'main.pxl':'''import "data.pxl" as data
fn make()->data.Choice[[int]]=data.Choice.Value([1])
fn read(value:data.Choice[[int]])->[int]{
 match value {data.Choice.Value(item)=>{return item},data.Choice.Empty=>{return []}}
}
effect fn main(){print(read(data.Choice.Value([])));print(read(make()))}
'''}, [[],[1]])

    def test_missing_evidence_nominal_mismatch_and_readonly_patterns_are_rejected(self):
        for source in (
            'enum Choice[T]{Empty,Value(item:T)}\nfn main(){let x=Choice.Empty}',
            'enum First{Empty}\nenum Second{Empty}\nfn main(){let x:First=Second.Empty}',
            'enum Choice[T]{Value(item:T)}\nfn main(){let x:Choice[int]=Choice::[string].Value("bad")}',
            'enum Choice{Value(items:[int])}\nfn main(){let x=Choice.Value([1]);match x {Choice.Value(items)=>{items.append(2)}}}',
        ):
            with self.subTest(source=source):
                result=self.compile(source)
                self.assertEqual(result['status'],'diagnostics',result)
