import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pixellang.bootstrap import compile_with
from pixellang.fileaccess import FileAccess
from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "png.pxl" as png
record Request { width: int, height: int, rgba: [int] }
fn main() {
    let request = jsonDecode[Request](input())
    print(png.Encode(request.width, request.height, request.rgba))
}
'''


class SelfHostedPNGTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {'main.pxl': DRIVER, 'png.pxl': (ROOT / 'selfhost/png.pxl').read_text()}
        cls.bytecode = compile_project(cls.files)[1].bytecode

    def encode(self, width, height, rgba, bytecode=None):
        vm = VM(bytecode or self.bytecode, input_text=json.dumps(dict(
            width=width, height=height, rgba=rgba)), max_steps=10_000_000)
        with patch('PIL.Image.Image.save', side_effect=AssertionError('host encoder')):
            return bytes(vm.run()[0])

    def check_image(self, data, width, height, rgba):
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        offset = 8
        kinds = []
        compressed = b''
        while offset < len(data):
            length = struct.unpack_from('>I', data, offset)[0]
            kind = data[offset + 4:offset + 8]
            payload = data[offset + 8:offset + 8 + length]
            checksum = struct.unpack_from('>I', data, offset + 8 + length)[0]
            self.assertEqual(checksum, zlib.crc32(kind + payload))
            kinds.append(kind)
            if kind == b'IDAT':
                compressed += payload
            offset += length + 12
        self.assertEqual(offset, len(data))
        self.assertEqual(kinds, [b'IHDR', b'IDAT', b'IEND'])
        expected = b''.join(b'\0' + bytes(rgba[y * width * 4:(y + 1) * width * 4])
                            for y in range(height))
        self.assertEqual(zlib.decompress(compressed), expected)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (width, height))
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.tobytes(), bytes(rgba))

    def test_independent_decoder_and_no_ancillary_chunks(self):
        for width, height in ((1, 1), (7, 3), (3, 8)):
            rgba = [(i * 71) % 256 for i in range(width * height * 4)]
            self.check_image(self.encode(width, height, rgba), width, height, rgba)

    def test_invalid_dimensions_lengths_and_bytes(self):
        for width, height, rgba, message in (
            (0, 1, [], 'dimensions'), (-1, 1, [], 'dimensions'),
            (32000001, 1, [], 'dimensions'), (10000, 10000, [], 'pixel limit'),
            (1, 1, [], 'length mismatch'), (1, 1, [0, 0, 0, 256], 'byte out of range'),
            (1, 1, [-1, 0, 0, 255], 'byte out of range'),
        ):
            with self.subTest(width=width, height=height, rgba=rgba):
                with self.assertRaisesRegex(PixelError, message):
                    self.encode(width, height, rgba)

    def test_pixellang_compiles_png_encoder(self):
        compiler_files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        seed = compile_project(compiler_files)[1].bytecode
        bytecode, _ = compile_with(seed, self.files)
        rgba = [255, 0, 8, 255, 20, 127, 240, 0]
        self.check_image(self.encode(2, 1, rgba, bytecode), 2, 1, rgba)


DECODE_DRIVER = '''import "png.pxl" as png
fn main() { print(png.Decode(jsonDecode[[int]](input()), 100000)) }
'''


def png_chunk(kind, payload):
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))


def png_data(width, height, rows, color=6):
    return (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, color, 0, 0, 0))
            + png_chunk(b'IDAT', zlib.compress(rows)) + png_chunk(b'IEND', b''))


class SelfHostedPNGDecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {'main.pxl': DECODE_DRIVER, 'png.pxl': (ROOT / 'selfhost/png.pxl').read_text()}
        cls.bytecode = compile_project(cls.files)[1].bytecode

    def decode(self, data, bytecode=None):
        with patch('PIL.Image.open', side_effect=AssertionError('host decoder')):
            return VM(bytecode or self.bytecode, input_text=json.dumps(list(data)), max_steps=10000000).run()[0]

    def test_all_filters_and_rgb(self):
        for channels, color in ((3, 2), (4, 6)):
            width, height = 3, 5
            stride = width * channels
            raw = bytes((i * 71 + i // 3) % 256 for i in range(stride * height))
            filtered = bytearray()
            for row in range(height):
                filtered.append(row)
                for column in range(stride):
                    i = row * stride + column
                    left = raw[i - channels] if column >= channels else 0
                    above = raw[i - stride] if row else 0
                    corner = raw[i - stride - channels] if row and column >= channels else 0
                    prediction = left + above - corner
                    distances = [abs(prediction - v) for v in (left, above, corner)]
                    paeth = (left, above, corner)[distances.index(min(distances))]
                    predictor = (0, left, above, (left + above) // 2, paeth)[row]
                    filtered.append((raw[i] - predictor) % 256)
            data = png_data(width, height, bytes(filtered), color)
            with Image.open(io.BytesIO(data)) as image:
                expected = list(image.convert('RGBA').tobytes())
            self.assertEqual(self.decode(data), dict(width=width, height=height, rgba=expected))

    def test_pillow_png_and_split_idat(self):
        image = Image.new('RGBA', (4, 3), (10, 30, 70, 255))
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        data = stream.getvalue()
        self.assertEqual(self.decode(data)['rgba'], list(image.tobytes()))
        packed = zlib.compress(b'\0\x01\x02\x03\xff')
        data = (png_data(1, 1, b'')[:33] + png_chunk(b'IDAT', packed[:3])
                + png_chunk(b'IDAT', packed[3:]) + png_chunk(b'IEND', b''))
        self.assertEqual(self.decode(data)['rgba'], [1, 2, 3, 255])

    def test_corruption_structure_and_expansion(self):
        good = png_data(1, 1, b'\0\x01\x02\x03\xff')
        broken = bytearray(good)
        broken[29] ^= 1
        cases = [
            (b'', 'signature'), (bytes(broken), 'CRC'), (good[:-1], 'chunk'),
            (good + b'x', 'IEND'), (good[:-12], 'IEND'),
            (png_data(1, 1, b'\x05\0\0\0\0'), 'filter'),
            (png_data(1, 1, b'\0'), 'length mismatch'),
            (png_data(1, 1, b'\0' * 6), 'limit'),
            (good[:33] + png_chunk(b'ABCD', b'') + good[33:], 'critical'),
            (good[:33] + png_chunk(b'PLTE', b'\0\0\0') * 2 + good[33:], 'palette'),
            (png_data(1000, 1000, b''), 'output limit'),
        ]
        for data, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(PixelError, message):
                self.decode(data)

    def test_pixellang_compiles_decoder(self):
        compiler_files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        seed = compile_project(compiler_files)[1].bytecode
        bytecode, _ = compile_with(seed, self.files)
        self.assertEqual(self.decode(png_data(1, 1, b'\0\x01\x02\x03\xff'), bytecode),
                         dict(width=1, height=1, rgba=[1, 2, 3, 255]))

    def test_selfhosted_codec_binary_file_roundtrip_and_replay(self):
        files = dict(self.files)
        files['main.pxl'] = '''import "png.pxl" as png
fn main() {
    let bytes = png.Encode(2, 1, [1, 2, 3, 255, 4, 5, 6, 255])
    print(writeBytes("program.png", bytes))
    let image = png.Decode(readBytes("program.png", 10000), 10000)
    print(image.rgba)
}'''
        compiler_files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        seed = compile_project(compiler_files)[1].bytecode
        bytecode, _ = compile_with(seed, files)
        expected = [True, [1, 2, 3, 255, 4, 5, 6, 255]]
        with tempfile.TemporaryDirectory() as directory:
            access = FileAccess(read_root=directory, write_root=directory)
            with patch('PIL.Image.open', side_effect=AssertionError('host decoder')), patch(
                'PIL.Image.Image.save', side_effect=AssertionError('host encoder')
            ):
                self.assertEqual(VM(bytecode, file_access=access, max_steps=10000000).run(), expected)
            with Image.open(Path(directory, 'program.png')) as image:
                self.assertEqual(list(image.tobytes()), expected[1])
            recorded = access.document()
        self.assertEqual(VM(bytecode, file_access=FileAccess.restore(recorded), max_steps=10000000).run(), expected)
