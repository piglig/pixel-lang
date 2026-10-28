import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources
from pixellang.codec import encode_token
from pixellang.model import PixelError
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedLiteralPixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT / 'selfhost' / name).read_text()
                 for name in ('literal_pixels.pxl', 'failures.pxl')}
        files['main.pxl'] = '''import "literal_pixels.pxl" as literals
record Request { kind: string, value: string }
effect fn main() {
 match jsonDecode::[Request](input()) {
  .Ok(request) => Encode(request),
  .Err(error) => print(error)
 }
}
effect fn Encode(request: Request) {
 if request.kind == "int" {
  match parseInt(request.value) { .Ok(value) => print(literals.Integer(value)), .Err(error) => print(error) }
 } else if request.kind == "float64" {
  match parseFloat64(request.value) { .Ok(value) => print(literals.Float(value)), .Err(error) => print(error) }
 } else if request.kind == "bool" { print(literals.Boolean(request.value == "true")) }
 else { match literals.Text(request.value) { .Ok(value) => print(value), .Err(error) => print(error) } }
}
'''
        cls.bytecode = compile_native_sources(files)

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
        error = self.encode('str', '界' * 21846)
        self.assertEqual(error['kind'], 'compiler')
        self.assertEqual(error['code'], 'compiler.invalid_data')
        self.assertIn('exceeds 65535 UTF-8 bytes', error['message'])
