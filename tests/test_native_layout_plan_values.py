"""Backend plans publish independent values and readonly capture permissions."""
import json
from pathlib import Path
import re
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "model.pxl" as model
import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "instances.pxl" as instances
import "closures.pxl" as closures
import "frames.pxl" as frames
import "schemas.pxl" as schemas
record Request { source:string, text:string, operation:string, files:map[string,string] }
record FrameOutput { functions:[model.FunctionFrame], symbols:[model.Symbol], types:[model.TypeInfo], entry:int, diagnostics:[model.Diagnostic] }
record SchemaOutput { schemas:[model.NominalSchema], types:[model.TypeInfo], diagnostics:[model.Diagnostic] }
record ClosureOutput { closures:[model.ClosureInstance], symbols:[model.Symbol], types:[model.TypeInfo], diagnostics:[model.Diagnostic] }
effect fn main() {
 match jsonDecode::[Request](input()) { .Ok(request)=>{Dispatch(request)}, .Err(error)=>{print(error.message)} }
}
effect fn Dispatch(request:Request) {
 var planned = closures.Build(instances.Build(checker.Check(types.Analyze(bindings.Bind(project.Build(request.files,request.source))))))
 let captured = planned.closures
 if len(captured)>0 && len(captured[0].captures)>0 {
  let permission = captured[0].captures[0].mutable
  var edited = captured
  edited[0].captures[0].mutable = !permission
  assert(captured[0].captures[0].mutable == permission)
 }
 if request.operation == "closures" {
  print(ClosureOutput{closures:captured,symbols:planned.named.checked.resolved.bound.symbols,types:planned.named.checked.resolved.types,diagnostics:planned.diagnostics})
 } else if request.operation == "frames" {
  let layout = frames.Build(&planned)
  if len(layout.functions)>0 {
   let count = len(layout.functions[0].slots)
   var edited = layout
   edited.functions[0].slots.append(model.BindingSlot{symbol:-1,typeId:-1,kind:"local",mutable:false})
   assert(len(layout.functions[0].slots)==count)
  }
  print(FrameOutput{functions:layout.functions,symbols:planned.named.checked.resolved.bound.symbols,types:planned.named.checked.resolved.types,entry:layout.entry,diagnostics:layout.diagnostics})
 } else {
  let layout = schemas.Build(&planned)
  if len(layout.schemas)>0 {
   let count = len(layout.schemas[0].fields)
   var edited = layout
   edited.schemas[0].fields.append(model.TypeField{name:"extra",typeId:-1})
   assert(len(layout.schemas[0].fields)==count)
  }
  print(SchemaOutput{schemas:layout.schemas,types:planned.named.checked.resolved.types,diagnostics:layout.diagnostics})
 }
}
'''


def sources():
    files = {'main.pxl': DRIVER}
    pending = re.findall(r'import "([^"]+)"', DRIVER)
    while pending:
        name = pending.pop()
        if name in files:
            continue
        files[name] = (ROOT / 'selfhost' / name).read_text()
        pending.extend(re.findall(r'import "([^"]+)"', files[name]))
    return files


class NativeLayoutPlanValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = compile_native_sources(sources())

    def request(self, operation, source):
        result = VM(self.code, input_text=json.dumps(dict(source='main.pxl', text='',
                    operation=operation, files={'main.pxl': source})),
                    max_steps=30_000_000, max_heap_items=32_000_000).run()[0]
        return json.loads(result) if isinstance(result, str) else result

    def test_closure_capture_slot_is_readonly_but_original_local_is_writable(self):
        result = self.request('frames', '''effect fn main() {
 var value = 1
 let read = fn() -> int = value
 value = 2
 print(read())
}''')
        self.assertEqual(result['diagnostics'], [])
        slots = [s for f in result['functions'] for s in f['slots']
                 if result['symbols'][s['symbol']]['name'] == 'value']
        self.assertEqual({s['kind']: s['mutable'] for s in slots}, {'local': True, 'capture': False})

    def test_anonymous_capture_metadata_is_readonly(self):
        result = self.request('closures', 'effect fn main() { var value=1; let read=fn()=value; value=2; print(read()) }')
        self.assertEqual(result['diagnostics'], [])
        capture = result['closures'][0]['captures'][0]
        self.assertFalse(capture['mutable'])
        self.assertTrue(result['symbols'][capture['symbol']]['mutable'])

    def test_recursive_nominal_schema_remains_finite_and_independent(self):
        result = self.request('schemas', 'record Node { children:[Node] }\neffect fn main() { print(Node{children:[]}) }')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(len(result['schemas']), 1)
        schema = result['schemas'][0]
        child_array = result['types'][schema['fields'][0]['typeId']]
        self.assertEqual(child_array['arguments'], [schema['typeId']])
