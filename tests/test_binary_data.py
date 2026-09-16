import struct
import unittest

from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM


class BinaryDataTests(unittest.TestCase):
    def run_source(self, body, **limits):
        bytecode = compile_project({'main.pxl': 'fn main() { ' + body + ' }'})[1].bytecode
        return VM(bytecode, **limits).run()

    def test_utf8_roundtrip(self):
        self.assertEqual(self.run_source('let text = "A界😀\\u0000"; print(utf8Encode(text)); print(utf8Decode(utf8Encode(text))); print(utf8Decode(([]: [int])))'), [list('A界😀\0'.encode()), 'A界😀\0', ''])

    def test_invalid_byte_sequences_and_ranges(self):
        for expression, code in [('utf8Decode([192, 128])', 'text.invalid_utf8'),
                                 ('utf8Decode([237, 160, 128])', 'text.invalid_utf8'),
                                 ('utf8Decode([240, 159])', 'text.invalid_utf8'),
                                 ('utf8Decode([256])', 'bytes.range'),
                                 ('float64FromBytes([-1])', 'bytes.range'),
                                 ('float64FromBytes([0])', 'bytes.length'),
                                 ('float64FromBytes([127, 240, 0, 0, 0, 0, 0, 0])', 'numeric.range')]:
            with self.subTest(expression=expression):
                self.assertEqual(self.run_source('try { ' + expression + ' } catch err { print(err.code) }'), [code])

    def test_exact_float_bits_and_signed_zero(self):
        for value in (0.0, -0.0, 1.0, -2.5, 5e-324, 1.7976931348623157e308):
            with self.subTest(value=value):
                values = self.run_source(f'let value = {value!r}; let bytes = float64Bytes(value); print(bytes); print(float64FromBytes(bytes))')
                self.assertEqual(values[0], list(struct.pack('>d', value)))
                self.assertEqual(struct.pack('>d', values[1]), struct.pack('>d', value))

    def test_encoding_enforces_heap_budget(self):
        with self.assertRaisesRegex(PixelError, 'Heap budget exceeded'):
            self.run_source('print(utf8Encode("界"))', max_heap_items=2)

    def test_static_argument_types(self):
        for expression in ('utf8Encode(1)', 'utf8Decode([true])', 'float64Bytes(1)', 'float64FromBytes("x")'):
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                self.run_source(expression)

    def test_crc_and_zlib_round_trip(self):
        self.assertEqual(self.run_source('print(crc32(utf8Encode("123456789"))); let bytes = utf8Encode("界😀abc"); print(utf8Decode(zlibDecompress(zlibCompress(bytes), 100))); print(zlibDecompress(zlibCompress(([]: [int])), 0))'), [3421780262, '界😀abc', []])

    def test_zlib_limits_corruption_and_trailing_data(self):
        cases = [
            ('zlibDecompress([1, 2, 3], 100)', 'bytes.compression'),
            ('zlibDecompress(zlibCompress(utf8Encode("hello")), 4)', 'bytes.limit'),
            ('zlibDecompress(zlibCompress(([]: [int])), -1)', 'bytes.limit'),
            ('zlibCompress([256])', 'bytes.range'),
            ('crc32([-1])', 'bytes.range'),
        ]
        for expression, code in cases:
            self.assertEqual(self.run_source('try { ' + expression + ' } catch err { print(err.code) }'), [code])
        for mutation in ('append(bytes, 0)', 'pop(bytes)'):
            self.assertEqual(self.run_source('let bytes = zlibCompress(utf8Encode("hello")); ' + mutation + '; try { zlibDecompress(bytes, 100) } catch err { print(err.code) }'), ['bytes.compression'])
        with self.assertRaisesRegex(PixelError, 'Heap budget exceeded'):
            self.run_source('zlibCompress(([]: [int]))', max_heap_items=4)

    def test_compression_static_argument_types(self):
        for expression in ('crc32("x")', 'zlibCompress([true])', 'zlibDecompress([1], true)', 'zlibDecompress([1])'):
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                self.run_source(expression)
