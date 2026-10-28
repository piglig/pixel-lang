import copy
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "assembler.pxl" as assembler
import "ir_state.pxl" as state
effect fn main() {
 match jsonDecode::[state.Output](input()) {.Ok(source)=>{Run(source)},.Err(error)=>{print(error.message)}}
}
effect fn Run(source:state.Output) {
 let built=assembler.Assemble(source)
 if len(built.bytecode.entry)>0 {
  let entry=built.bytecode.entry[0]
  let count=len(built.bytecode.functions[entry].instructions)
  var changed=built
  changed.bytecode.functions[entry].instructions.pop()
  changed.bytecode.entry.append("missing")
  assert(len(built.bytecode.entry)==1)
  assert(len(built.bytecode.functions[entry].instructions)==count)
 }
 print(jsonFrom(built))
}
'''


class SelfHostedAssemblerTests(unittest.TestCase):
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
        cls.bytecode = compile_native_sources(files)

    def assemble(self, ir, diagnostics=None):
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'ir': ir, 'diagnostics': diagnostics or [],
        }), max_steps=20_000_000, max_depth=1024)
        with patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            return json.loads(vm.run()[0])

    def ir(self, code, extra=None):
        span = {'source': 'test.pxl', 'kind': 'text', 'points': [[0, 0], [1, 0]],
                'min': [0, 0], 'max': [1, 0], 'start': {'line': 1, 'column': 1, 'offset': 0},
                'end': {'line': 1, 'column': 2, 'offset': 1}}
        instructions = [dict(op=op, arg=arg, module=0, node=0, span=span) for op, arg in code]
        records = {'Error': {k: 'str' for k in ('kind', 'code', 'message', 'path', 'operation', 'expected', 'actual')}}
        functions = {'0': dict(params=[], captures=[], locals=0, result='unit', instructions=instructions)}
        functions.update(extra or {})
        return dict(version='0.9', coordinateFrames={}, effectFunctions=['0'], flows={},
                    records=records, enums={}, entry=['0'], functions=functions)

    def expected_bytecode(self, ir, constants, instructions):
        function = ir['functions']['0']
        origin = {key: function['instructions'][0][key] for key in ('module', 'node', 'span')}
        return dict(magic='PIXELVM', vm_version='0.9', language_version='0.9',
                    compiler_version='0.9.0', coordinateFrames={}, effectFunctions=['0'], flows={},
                    records=ir['records'], enums={}, entry=['0'], constants=constants,
                    functions={'0': dict(function, instructions=[
                        dict(origin, op=op, arg=arg) for op, arg in instructions])})

    def test_constant_identity_and_exact_bytecode(self):
        values = [('int', 1), ('bool', True), ('float64', 1.0), ('str', '1'),
                  ('float64', 0.0), ('float64', -0.0), ('int', 1), ('int', -9223372036854775808)]
        code = []
        for typ, value in values:
            code += [('CONST', {'type': typ, 'value': value}), ('PRINT', None)]
        code += [('CONST', {'type': 'int', 'value': 0}), ('RETURN', None)]
        ir = self.ir(code)
        original = copy.deepcopy(ir)
        result = self.assemble(ir)
        self.assertEqual(result['diagnostics'], [])
        expected_constants = [dict(type=typ, value=value) for typ, value in
                              values[:6] + [values[7], ('int', 0)]]
        expected_code = [(op, arg) for index in [0, 1, 2, 3, 4, 5, 0, 6]
                         for op, arg in [('PUSH', index), ('PRINT', None)]]
        expected_code += [('PUSH', 7), ('RETURN', None)]
        expected = self.expected_bytecode(ir, expected_constants, expected_code)
        # JSON spelling distinguishes bool/int/float and positive/negative zero.
        self.assertEqual(json.dumps(result['bytecode'], sort_keys=True), json.dumps(expected, sort_keys=True))
        self.assertEqual(ir, original)
        self.assertEqual(len(result['bytecode']['constants']), 8)
        self.assertEqual(VM(result['bytecode']).run(), [value for _, value in values])

    def test_forward_branch_labels_are_removed(self):
        ir = self.ir([('CONST', {'type': 'bool', 'value': False}), ('JUMP_IF_FALSE', 9),
                      ('CONST', {'type': 'str', 'value': 'wrong'}), ('PRINT', None),
                      ('LABEL', 9), ('CONST', {'type': 'str', 'value': 'ok'}), ('PRINT', None),
                      ('CONST', {'type': 'int', 'value': 0}), ('RETURN', None)])
        result = self.assemble(ir)
        self.assertEqual(result['diagnostics'], [])
        expected = self.expected_bytecode(ir, [
            dict(type='bool', value=False), dict(type='str', value='wrong'),
            dict(type='str', value='ok'), dict(type='int', value=0),
        ], [('PUSH', 0), ('JUMP_IF_FALSE', 4), ('PUSH', 1), ('PRINT', None),
            ('PUSH', 2), ('PRINT', None), ('PUSH', 3), ('RETURN', None)])
        self.assertEqual(result['bytecode'], expected)
        self.assertEqual(VM(result['bytecode']).run(), ['ok'])

    def test_invalid_labels_clear_executable_functions(self):
        for code, expected in [
            ([('JUMP', 1), ('RETURN', None)], 'Unknown label'),
            ([('LABEL', 1), ('LABEL', 1), ('RETURN', None)], 'Duplicate label'),
            ([('JUMP', 1), ('RETURN', None), ('LABEL', 1)], 'past function body'),
        ]:
            with self.subTest(expected=expected):
                result = self.assemble(self.ir(code))
                self.assertIn(expected, result['diagnostics'][0]['message'])
                self.assertEqual(result['diagnostics'][0]['source'], 'test.pxl')
                self.assertEqual(result['bytecode']['functions'], {})
                self.assertEqual(result['bytecode']['entry'], [])

    def test_prior_diagnostics_prevent_assembly(self):
        diagnostic = dict(source='test.pxl', phase='ir', message='unsupported', start=0, end=1, line=1, column=1)
        result = self.assemble(self.ir([('RETURN', None)]), [diagnostic])
        self.assertEqual(result['diagnostics'], [diagnostic])
        self.assertEqual(result['bytecode']['functions'], {})
        self.assertEqual(result['bytecode']['entry'], [])
