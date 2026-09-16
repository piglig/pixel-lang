import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.codec import encode_token
from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedLiteralPixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "literal_pixels.pxl" as literals
record Request { kind: string, value: string }
fn main() {
 let request = jsonDecode[Request](input())
 if request.kind == "int" { print(literals.Integer(parseInt(request.value))) }
 else if request.kind == "float64" { print(literals.Float(parseFloat64(request.value))) }
 else if request.kind == "bool" { print(literals.Boolean(request.value == "true")) }
 else { print(literals.Text(request.value)) }
}
'''
        cls.bytecode = compile_project(files)[1].bytecode

    def encode(self, kind, value):
        text = ('true' if value else 'false') if kind == 'bool' else str(value)
        vm = VM(self.bytecode, input_text=json.dumps({'kind': kind, 'value': text}),
                max_steps=10_000_000, max_depth=1024)
        with patch('pixellang.codec.encode_token', side_effect=AssertionError('delegated')):
            return vm.run()[0]

    def test_integer_boundaries_and_word_carries(self):
        for value in (-2**63, -65537, -65536, -32769, -32768, -1, 0, 32767, 32768, 65535, 65536, 2**63-1):
            with self.subTest(value=value):
                self.assertEqual(self.encode('int', value), encode_token('int', value))

    def test_utf8_lengths_padding_and_boolean(self):
        for value in ('', 'a', 'ab', 'abc', '界😀', '\0x'):
            with self.subTest(value=value):
                self.assertEqual(self.encode('str', value), encode_token('str', value))
        for value in (False, True):
            self.assertEqual(self.encode('bool', value), encode_token('bool', value))

    def test_float_patterns(self):
        for value in (0.0, -0.0, 1.0, -2.5, 5e-324, 1.7976931348623157e308):
            with self.subTest(value=value):
                self.assertEqual(self.encode('float64', value), encode_token('float64', value))

    def test_length_limit_counts_utf8_bytes(self):
        with self.assertRaisesRegex(PixelError, 'exceeds 65535 UTF-8 bytes'):
            self.encode('str', '界' * 21846)
