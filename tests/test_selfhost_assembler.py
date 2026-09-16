import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.backend import assemble
from pixellang.project import compile_project
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedAssemblerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "assembler.pxl" as assembler
import "ir_state.pxl" as state
fn main() { print(jsonFrom(assembler.Assemble(jsonDecode[state.Output](input())))) }
'''
        cls.bytecode = compile_project(files)[1].bytecode

    def assemble(self, ir, diagnostics=None):
        vm = VM(self.bytecode, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            'ir': ir, 'diagnostics': diagnostics or [],
        }), max_steps=20_000_000, max_depth=1024)
        with patch('pixellang.backend.assemble', side_effect=AssertionError('delegated')), patch(
            'pixellang.backend.lower', side_effect=AssertionError('delegated')
        ), patch('pixellang.text.parse_text', side_effect=AssertionError('delegated')):
            return json.loads(vm.run()[0])

    def ir(self, code, extra=None):
        span = {'source': 'test.pxl', 'kind': 'text', 'points': [[0, 0], [1, 0]],
                'min': [0, 0], 'max': [1, 0], 'start': {'line': 1, 'column': 1, 'offset': 0},
                'end': {'line': 1, 'column': 2, 'offset': 1}}
        instructions = [dict(op=op, arg=arg, module=0, node=0, span=span) for op, arg in code]
        records = {'Error': {k: 'str' for k in ('kind', 'code', 'message', 'path', 'operation', 'expected', 'actual')}}
        functions = {'0': dict(params=[], captures=[], locals=0, result='unit', instructions=instructions)}
        functions.update(extra or {})
        return dict(version='0.8', records=records, enums={}, entry=['0'], functions=functions)

    def test_constant_identity_and_host_assembly_parity(self):
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
        self.assertEqual(result['bytecode'], assemble(ir))
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
        self.assertEqual(result['bytecode'], assemble(ir))
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
