import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from pixellang.fileaccess import FileAccess
from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM


class BinaryFileTests(unittest.TestCase):
    def compile(self, body):
        return compile_project({'main.pxl': 'fn main() { ' + body + ' }'})[1].bytecode

    def test_raw_bytes_mutation_and_replay(self):
        code = self.compile('writeBytes("image.bin", [0, 255, 192, 128]); let data = readBytes("image.bin", 20); data[0] = 10; print(data)')
        with tempfile.TemporaryDirectory() as directory:
            access = FileAccess(read_root=directory, write_root=directory)
            self.assertEqual(VM(code, file_access=access).run(), [[10, 255, 192, 128]])
            self.assertEqual(Path(directory, 'image.bin').read_bytes(), b'\0\xff\xc0\x80')
            self.assertEqual(access.effects[1]['result'], [0, 255, 192, 128])
            saved = access.document()
        restored = FileAccess.restore(saved)
        self.assertEqual(VM(code, file_access=restored).run(), [[10, 255, 192, 128]])
        self.assertEqual(restored.effects[1]['result'], [0, 255, 192, 128])

    def test_permissions_paths_limits_and_invalid_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'data').write_bytes(b'abc')
            Path(directory, 'link').symlink_to(Path(directory, 'data'))
            for expression, expected in (
                ('readBytes("data", 2)', 'io.too_large'),
                ('readBytes("../data", 10)', 'io.invalid_path'),
                ('readBytes("link", 10)', 'io.access_denied'),
                ('readBytes("missing", 10)', 'io.not_found'),
                ('readBytes("data", -1)', 'bytes.limit'),
                ('writeBytes("out", [256])', 'bytes.range'),
            ):
                code = self.compile('try { ' + expression + ' } catch err { print(err.code) }')
                access = FileAccess(read_root=directory, write_root=directory)
                with self.subTest(expression=expression):
                    self.assertEqual(VM(code, file_access=access).run(), [expected])
            code = self.compile('try { readBytes("data", 10) } catch err { print(err.code) }; try { writeBytes("out", [1]) } catch err { print(err.code) }')
            self.assertEqual(VM(code).run(), ['io.access_denied', 'io.access_denied'])

    def test_restore_rejects_invalid_bytes_and_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'data').write_bytes(b'ab')
            access = FileAccess(read_root=directory)
            access.perform('readBytes', 'data', 10, 0)
            original = access.document()
            for value in ([True], [-1], [256], 'ab'):
                document = deepcopy(original)
                document['effects'][0]['result'] = value
                with self.assertRaisesRegex(PixelError, 'Invalid recorded file bytes'):
                    FileAccess.restore(document)
            large = FileAccess(read_root=directory, budget=8192)
            with self.assertRaisesRegex(PixelError, 'budget'):
                large.perform('readBytes', 'data', 10, 0)

    def test_static_types(self):
        for expression in ('readBytes("x")', 'readBytes(1, 2)', 'readBytes("x", true)', 'writeBytes("x", "text")', 'writeBytes("x", [true])'):
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                self.compile(expression)
