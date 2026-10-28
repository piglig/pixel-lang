"""IR value ownership, nested initializer publication and native execution."""
import json
from pathlib import Path
import re
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "instances.pxl" as instances
import "closures.pxl" as closures
import "ir.pxl" as ir
import "assembler.pxl" as assembler
record Request { source:string, operation:string, files:map[string,string] }
effect fn main() {
 match jsonDecode::[Request](input()) {
  .Ok(request) => { Dispatch(request) },
  .Err(error) => { print(error.message) }
 }
}
effect fn Dispatch(request:Request) {
 var planned=closures.Build(instances.Build(checker.Check(types.Analyze(bindings.Bind(project.Build(request.files,request.source))))))
 if request.operation=="missing-expression" { planned.named.checked.expressions=[] }
 let lowered=ir.Build(&planned)
 if len(lowered.ir.entry)>0 {
  let entry=lowered.ir.entry[0]
  let count=len(lowered.ir.functions[entry].instructions)
  var edited=lowered
  edited.ir.entry.append("missing")
  edited.ir.functions[entry].instructions.pop()
  assert(len(lowered.ir.entry)==1)
  assert(len(lowered.ir.functions[entry].instructions)==count)
 }
 if request.operation=="bytecode" { print(assembler.Assemble(lowered)) }
 else { print(lowered) }
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


class NativeIRValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = compile_native_sources(sources())

    def lower(self, source, operation='bytecode'):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        result = VM(self.code, input_text=json.dumps(dict(source='main.pxl',
                    operation=operation, files=files)), max_steps=80_000_000,
                    max_heap_items=32_000_000, max_depth=1024).run()[0]
        result = json.loads(result) if isinstance(result, str) else result
        # Direct record output transports typed json fields as encoded text.
        # Decode only those schema-defined fields before invoking the VM.
        program = result['bytecode'] if operation == 'bytecode' else result['ir']
        if operation == 'bytecode':
            program['constants'] = [json.loads(value) for value in program['constants']]
        for function in program['functions'].values():
            for instruction in function['instructions']:
                instruction['arg'] = json.loads(instruction['arg'])
        return result

    def run_program(self, source, expected):
        result = self.lower(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode'], max_steps=1_000_000).run(), expected)
        return result['bytecode']

    def test_nested_value_update_and_inout_emit_complete_program(self):
        self.run_program('''record Box { values:[int] }
fn Add(value:inout int) { value+=4 }
effect fn main() {
 let original=Box{values:[1,2]}
 var edited=original
 Add(&edited.values[0])
 print(original.values)
 print(edited.values)
}''', [[1, 2], [5, 2]])

    def test_match_callback_and_loop_labels_write_back(self):
        self.run_program('''enum Item { Number(value:int), Skip }
fn Sum(values:[Item]) -> int {
 var total=0
 for item in values {
  match item {
   Item.Number(value) => { if value<0 { continue }; total+=value },
   Item.Skip => {}
  }
 }
 return total
}
effect fn main() { print(Sum([Item.Number(2),Item.Skip,Item.Number(-1),Item.Number(5)])) }
''', [7])

    def test_value_captures_and_generic_function_frames(self):
        self.run_program('''fn Identity[T](value:T) -> T = value
effect fn main() {
 var offset=3
 let add=fn(value:int) -> int = Identity(value+offset)
 offset=9
 print(add(4))
 print(offset)
}''', [7, 9])

    def test_constructor_functions_and_nested_flow_keep_state(self):
        self.run_program('''fn Add(value:int, delta:int) -> int = value+delta
flow Counter(initial:int) step(delta:int) -> int {
 state value:int=initial
 let add=Add(value,delta)
 next value=add
 output add
}
flow Parent(initial:int) step(delta:int) -> int {
 instance child=Counter(initial)
 output child.step(delta)
}
effect fn main() {
 let instance=Parent(10)
 print(instance.step(2))
 print(instance.step(3))
}''', [12, 15])

    def test_initializer_diagnostics_reach_parent_and_clear_executable(self):
        result = self.lower('''flow Counter(initial:int) step(delta:int) -> int {
 state value:int=initial+1
 next value=value
 output value
}
effect fn main() { let instance=Counter(10); print(instance.step(2)) }
''', 'missing-expression')
        self.assertTrue(result['diagnostics'])
        self.assertTrue(any(d['phase'] == 'ir' and d['line'] == 2 and
                            'expression type' in d['message'] for d in result['diagnostics']))
        self.assertEqual(result['ir']['entry'], [])
        self.assertEqual(result['ir']['functions'], {})
        self.assertEqual(result['ir']['flows'], {})

    def test_missing_flow_output_type_reports_diagnostic_without_executable(self):
        result = self.lower('''flow Pass() step(value:int) -> int {
 output value
}
effect fn main() { let instance=Pass(); print(instance.step(2)) }
''', 'missing-expression')
        self.assertTrue(any(d['phase'] == 'ir' and d['line'] == 2 and
                            'Missing expression type' in d['message']
                            for d in result['diagnostics']))
        self.assertEqual(result['ir']['entry'], [])
        self.assertEqual(result['ir']['functions'], {})
        self.assertEqual(result['ir']['flows'], {})

    def test_spans_remain_attached_to_imported_instructions(self):
        code = self.run_program({
            'main.pxl': 'import "lib.pxl" as lib\neffect fn main() { print(lib.Double(4)) }',
            'lib.pxl': 'export fn Double(value:int) -> int {\n return value*2\n}',
        }, [8])
        instructions = [i for f in code['functions'].values() for i in f['instructions']]
        multiply = next(i for i in instructions if i['op'] == 'MUL')
        self.assertEqual(multiply['span']['source'], 'lib.pxl')
        self.assertEqual(multiply['span']['start']['line'], 2)
