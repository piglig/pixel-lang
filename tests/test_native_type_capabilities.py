"""Capabilities inspect complete container values, including empty maps."""
import json
from pathlib import Path
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
record Capability { name:string, comparable:bool }
effect fn RunTest()->Result[int,Error] {
 let files=(jsonDecode::[map[string,string]](input()))?
 var resolved=types.Analyze(bindings.Bind(project.Build(files,"main.pxl")))
 print(resolved.diagnostics)
 if len(resolved.diagnostics)>0 {return .Ok(0)}
 for signature in resolved.signatures {
  let name=resolved.bound.project.modules[signature.module].syntax.nodes[signature.node].text
  if name=="main" {continue}
  let argument=resolved.types[signature.typeId].arguments[0]
  print(Capability{name,comparable:types.Equatable(argument,&resolved)})
 }

 return .Ok(0)
}
effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
'''


def driver_sources():
    files = {name: (ROOT / 'selfhost' / name).read_text() for name in
             ('model.pxl', 'lexer.pxl', 'parser.pxl', 'project.pxl', 'bindings.pxl', 'types.pxl')}
    return {**files, 'main.pxl': DRIVER}


class NativeTypeCapabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = compile_native_sources(driver_sources())

    def diagnostics(self, source):
        return VM(self.code, input_text=json.dumps({'main.pxl': source}),
                  max_steps=10_000_000, max_heap_items=16_000_000).run()[0]

    def test_unit_is_rejected_in_value_type_arguments_before_codegen(self):
        for annotation in ('Result[unit,string]', 'Result[int,unit]',
                           'Option[unit]', 'map[string,unit]', '[unit]',
                           'Box[unit]', 'Option[[unit]]', 'fn(unit)',
                           'fn(inout unit)'):
            with self.subTest(annotation=annotation):
                diagnostics = self.diagnostics(
                    'record Box[T] { value:T }; record Use { value:' + annotation + ' }; fn main() {}')
                self.assertTrue(diagnostics)
                self.assertTrue(all(d['phase'] == 'type' for d in diagnostics), diagnostics)
                self.assertTrue(any('unit is only a function result' in d['message']
                                    for d in diagnostics), diagnostics)

    def test_unit_result_callbacks_remain_values(self):
        self.assertEqual(self.diagnostics('''record Box[T] { value:T }
record Use { callback:fn()->unit, optional:Option[fn()], boxed:Box[fn(int)] }
fn main()->unit {}
'''), [])

    def test_unit_fields_parameters_and_explicit_specializations_are_rejected(self):
        for source in (
            'record Bad { value:unit }; fn main() {}',
            'enum Bad { Value(value:unit) }; fn main() {}',
            'fn bad(value:unit) {}; fn main() {}',
            'fn generic[T]() {}; fn main() { generic::[unit]() }',
            'fn main() { let value=Result::[unit,string].Err("error") }',
            'fn main() { let value=jsonDecode::[unit]("null") }',
        ):
            with self.subTest(source=source):
                diagnostics = self.diagnostics(source)
                self.assertTrue(any(d['phase'] == 'type' and
                                    'unit is only a function result' in d['message']
                                    for d in diagnostics), diagnostics)

    def test_map_values_participate_in_comparability_through_nested_types(self):
        source = '''record Box[T] { value:T }
enum Choice[T] { Empty, Value(value:T) }
fn numbers(value:map[string,int]){}
fn arrays(value:map[string,[int]]){}
fn records(value:map[string,Box[int]]){}
fn choices(value:map[string,Choice[int]]){}
fn callbacks(value:map[string,fn()->int]){}
fn nested(value:map[string,map[int,fn()->int]]){}
fn recordCallbacks(value:Box[map[string,fn()->int]]){}
fn inactiveCallbacks(value:map[string,Choice[fn()->int]]){}
fn jsonValues(value:map[string,json]){}
fn main(){}
'''
        output = VM(self.code, input_text=json.dumps({'main.pxl': source}),
                    max_steps=10_000_000, max_heap_items=16_000_000).run()
        self.assertEqual(output[0], [])
        self.assertEqual({item['name']: item['comparable'] for item in output[1:]}, {
            'numbers': True, 'arrays': True, 'records': True, 'choices': True,
            'callbacks': False, 'nested': False, 'recordCallbacks': False,
            'inactiveCallbacks': False, 'jsonValues': False,
        })
