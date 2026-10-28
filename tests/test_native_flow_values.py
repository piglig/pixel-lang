"""Flow plans and constant trees retain their values across compiler passes."""
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
record Output { flows:[model.TypedFlow], diagnostics:[model.Diagnostic] }
effect fn main() {
 match jsonDecode::[map[string,string]](input()) {
  .Ok(files) => { Dispatch(files) },
  .Err(error) => { print(error.message) }
 }
}
effect fn Dispatch(files:map[string,string]) {
 let checked = checker.Check(types.Analyze(bindings.Bind(project.Build(files,"main.pxl"))))
 let published = checked.flows
 if len(published) > 0 {
  let before = jsonFrom(published)
  var edited = published
  edited[0].graph.order.append(-1)
  edited[0].graph.dependencies.append(model.FlowDependency{source:-1,target:-1,reference:-1})
  for key, value in edited[0].initializers {
   edited[0].initializers[key].children.append(value)
  }
  assert(jsonStringify(jsonFrom(published)) == jsonStringify(before))
 }
 print(Output{flows:published,diagnostics:checked.diagnostics})
}
'''


class NativeFlowValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {'main.pxl': DRIVER}
        pending = re.findall(r'import "([^"]+)"', DRIVER)
        while pending:
            name = pending.pop()
            if name in files:
                continue
            files[name] = (ROOT / 'selfhost' / name).read_text()
            pending.extend(re.findall(r'import "([^"]+)"', files[name]))
        cls.code = compile_native_sources(files)

    def check(self, source):
        result = VM(self.code, input_text=json.dumps({'main.pxl': source}),
                    max_steps=30_000_000, max_heap_items=32_000_000).run()[0]
        return json.loads(result) if isinstance(result, str) else result

    def test_nested_constants_are_published_after_initialization(self):
        result = self.check('''record Data { values:[int], names:map[string,string] }
flow Stored() step() -> Data {
 state data:Data = Data{values:[1,2+3],names:map[string,string]{"key":"value"}}
 next data = data
 output data
}
fn main() {}
''')
        self.assertEqual(result['diagnostics'], [])
        flow = result['flows'][0]
        self.assertTrue(flow['stateful'])
        constant = next(iter(flow['initializers'].values()))
        fields = dict(zip(constant['fields'], constant['children']))
        # The native transport prints statically typed json fields as JSON text.
        self.assertEqual([json.loads(c['value']) for c in fields['values']['children']], [1, 5])
        self.assertEqual([json.loads(c['value']) for c in fields['names']['children']], ['key', 'value'])

    def test_statefulness_propagates_through_child_instances(self):
        result = self.check('''flow Child() step(delta:int) -> int {
 state value:int = 0
 next value = delta
 output value
}
flow Parent() step(delta:int) -> int {
 instance child = Child()
 output child.step(delta)
}
fn main() {}
''')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual([f['stateful'] for f in result['flows']], [True, True])
        self.assertEqual(len(result['flows'][1]['instances']), 1)
        self.assertNotIn('endpoints', result['flows'][1])
        self.assertNotIn('connections', result['flows'][1]['graph'])
        self.assertEqual(len(result['flows'][1]['graph']['dependencies']), 2)

    def test_recursive_dependencies_are_rejected(self):
        result = self.check('''flow A() step(value:int) -> int {
 instance child = B()
 output child.step(value)
}
flow B() step(value:int) -> int {
 instance child = A()
 output child.step(value)
}
fn main() {}
''')
        errors = [d for d in result['diagnostics'] if 'recursive instance' in d['message']]
        self.assertEqual(len(errors), 2)

    def test_constant_failure_retains_nested_expression_location(self):
        result = self.check('''flow Broken() step() -> [int] {
 state values:[int] = [1,
     2 / 0]
 next values = values
 output values
}
fn main() {}
''')
        errors = [d for d in result['diagnostics'] if 'Invalid state initializer' in d['message']]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['line'], 3)
        self.assertEqual(result['flows'][0]['initializers'], {})

    def test_effect_violation_is_reported_at_definition_without_poisoning_callers(self):
        result = self.check('''fn Emit(value:int) -> int { print(value); return value }
fn Relay(value:int) -> int = Emit(value)
flow Pipeline() step(value:int) -> int {
 let relay = Relay(value)
 output relay
}
fn main() {}
''')
        errors = [d for d in result['diagnostics'] if d['phase'] == 'effect']
        self.assertEqual(len(errors), 1, errors)
        self.assertIn('print', errors[0]['message'])
        self.assertEqual(errors[0]['line'], 1)
