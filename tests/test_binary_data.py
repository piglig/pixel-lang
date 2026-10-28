import struct
import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM


class BinaryDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def run_source(self, body, **limits):
        response = self.compiler.request('compile', files={'main.pxl': 'effect fn Run() -> Result[bool, Error] { ' + body + '; return .Ok(true) }\neffect fn main(){match Run(){.Ok(done)=>{},.Err(error)=>{print(error.code)}}}'})
        self.assertEqual(response['status'], 'ok', response)
        bytecode = response['result']['bytecode']
        return VM(bytecode, **limits).run()

    def test_utf8_roundtrip(self):
        self.assertEqual(self.run_source('let text = "A界😀\\u0000"; print(utf8Encode(text)); print(utf8Decode(utf8Encode(text))?); print(utf8Decode(([]: [int]))?)'), [list('A界😀\0'.encode()), 'A界😀\0', ''])

    def test_invalid_byte_sequences_and_ranges(self):
        for expression, code in [('utf8Decode([192, 128])', 'text.invalid_utf8'),
                                 ('utf8Decode([237, 160, 128])', 'text.invalid_utf8'),
                                 ('utf8Decode([240, 159])', 'text.invalid_utf8'),
                                 ('utf8Decode([256])', 'bytes.range'),
                                 ('float64FromBytes([-1])', 'bytes.range'),
                                 ('float64FromBytes([0])', 'bytes.length'),
                                 ('float64FromBytes([127, 240, 0, 0, 0, 0, 0, 0])', 'numeric.range')]:
            with self.subTest(expression=expression):
                self.assertEqual(self.run_source('let value = ' + expression + '?'), [code])

    def test_exact_float_bits_and_signed_zero(self):
        for value in (0.0, -0.0, 1.0, -2.5, 5e-324, 1.7976931348623157e308):
            with self.subTest(value=value):
                values = self.run_source(f'let value = {value!r}; let bytes = float64Bytes(value); print(bytes); print(float64FromBytes(bytes)?)')
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
        self.assertEqual(self.run_source('print(crc32(utf8Encode("123456789"))); let bytes = utf8Encode("界😀abc"); print(utf8Decode(zlibDecompress(zlibCompress(bytes)?, 100)?)?); print(zlibDecompress(zlibCompress(([]: [int]))?, 0)?)'), [3421780262, '界😀abc', []])

    def test_compression_preserves_independent_input_values(self):
        self.assertEqual(self.run_source('''var bytes = [65, 66, 67]
var copy = bytes
let encoded = zlibCompress(bytes)?
print(bytes)
copy[0] = 90
bytes[1] = 88
print(utf8Decode(zlibDecompress(encoded, 3)?)?)
print(bytes); print(copy)'''), [[65, 66, 67], 'ABC', [65, 88, 67], [90, 66, 67]])

    def test_compression_fault_preserves_input(self):
        self.assertEqual(self.run_source('''var bytes=[65,256]
match zlibCompress(bytes) {
 .Ok(value)=>{print("unexpected")},
 .Err(error)=>{print(error.code);print(error.operation)}
}
print(bytes); bytes[1]=66; print(utf8Decode(zlibDecompress(zlibCompress(bytes)?,2)?)?)'''),
                         ['bytes.range','zlibCompress',[65,256],'AB'])

    def test_compression_does_not_grant_output_write_permissions(self):
        with self.assertRaises(PixelError) as raised:
            self.run_source('let packed=zlibCompress([65])?; packed.append(0)')
        self.assertIn('readonly', str(raised.exception))

    def test_decompression_results_have_value_semantics(self):
        self.assertEqual(self.run_source('''let packed = zlibCompress([65,66])?
let original = zlibDecompress(packed,2)?
var changed = original
changed[0] = 90
print(changed);print(original);print(zlibDecompress(packed,2)?)'''), [[90,66],[65,66],[65,66]])
        with self.assertRaises(PixelError) as raised:
            self.run_source('let values=zlibDecompress(zlibCompress([65])?,1)?; values.append(66)')
        self.assertIn('readonly', str(raised.exception))

    def test_zlib_limits_corruption_and_trailing_data(self):
        for expression, code in [
            ('zlibDecompress([1,2,3],100)', 'bytes.compression'),
            ('zlibDecompress(zlibCompress(utf8Encode("hello"))?,4)', 'bytes.limit'),
            ('zlibDecompress(zlibCompress(([]:[int]))?,-1)', 'bytes.limit'),
        ]:
            with self.subTest(expression=expression):
                self.assertEqual(self.run_source('let value='+expression+'?'), [code])
        self.assertEqual(self.run_source('let value=zlibCompress([256])?'), ['bytes.range'])
        for expression in ['crc32([-1])']:
            with self.subTest(expression=expression), self.assertRaises(PixelError) as raised:
                self.run_source('let value='+expression)
            self.assertEqual(raised.exception.code,'bytes.range')
        for mutation in ('bytes.append(0)', 'let removed=bytes.pop()'):
            self.assertEqual(self.run_source('var bytes=zlibCompress(utf8Encode("hello"))?; '+mutation+'; let decoded=zlibDecompress(bytes,100)?'), ['bytes.compression'])
        with self.assertRaisesRegex(PixelError,'Heap budget exceeded'):
            self.run_source('let packed=zlibCompress(([]:[int]))?',max_heap_items=4)

    def test_compression_static_argument_types(self):
        for expression in ('crc32("x")', 'zlibCompress([true])?', 'zlibDecompress([1], true)', 'zlibDecompress([1])'):
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                self.run_source(expression)
