"""The immutable checker publishes complete typed flow metadata."""
import json
from pathlib import Path
import re
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

DRIVER = '''import "checker.pxl" as checker
import "types.pxl" as types
import "project.pxl" as project
import "bindings.pxl" as bindings
effect fn main() {
 match jsonDecode::[map[string,string]](input()) {
  .Ok(files)=>{print(checker.Check(types.Analyze(bindings.Bind(project.Build(files,"main.pxl")))))},
  .Err(error)=>{print(error.message)}
 }
}'''


class ImmutableCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / 'selfhost'
        files = {'main.pxl': DRIVER}
        def add(name):
            if name in files:
                return
            files[name] = (root / name).read_text()
            for child in re.findall(r'^import "([^"]+)"', files[name], re.M):
                add(child)
        for name in ('checker.pxl', 'project.pxl', 'bindings.pxl'):
            add(name)
        cls.image = compile_native_sources(files)

    def check(self, source):
        return VM(self.image, input_text=json.dumps({'main.pxl': source}),
                  max_steps=40_000_000, max_depth=1024,
                  max_heap_items=16_000_000, max_heap_objects=1_000_000,
                  max_output_chars=64_000_000).run()[0]

    def test_flow_initializer_constants_and_failures(self):
        checked = self.check('flow F() step(input: [int]) -> [int] { state values: [int] = [1 + 2, 4]; next values = input; output values }\nfn main() {}')
        self.assertEqual(checked['diagnostics'], [])
        initial = next(iter(checked['flows'][0]['initializers'].values()))
        self.assertEqual(initial['kind'], 'array')
        self.assertEqual([json.loads(item['value']) for item in initial['children']], [3, 4])
        for expression in ('9223372036854775807 + 1', '1 / 0'):
            with self.subTest(expression=expression):
                checked = self.check('flow F() step(input: int) -> int { state value: int = ' + expression + '; next value = input; output value }\nfn main() {}')
                self.assertTrue(any('Invalid state initializer' in d['message'] for d in checked['diagnostics']))

    def test_nested_flow_statefulness_propagates(self):
        source = 'flow Inner() step(delta: int) -> int { state total: int = 0; next total = delta; output total }\nflow Outer() step(delta: int) -> int { instance inner = Inner(); output inner.step(delta) }\n'
        checked = self.check(source + 'fn main() {}')
        self.assertEqual(checked['diagnostics'], [])
        self.assertEqual([flow['stateful'] for flow in checked['flows']], [True, True])
        errors = self.check(source + 'fn main() { let invalid = Outer }')['diagnostics']
        self.assertTrue(any('must be constructed directly' in d['message'] for d in errors))
