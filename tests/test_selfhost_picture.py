import io
import json
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pixellang.bootstrap import compile_with, read_bytecode_stream
from pixellang.picture import decode_picture
from pixellang.project import compile_project
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "picture.pxl" as picture
import "spatial_layout.pxl" as layout
record Request { root: layout.Source, modules: map[layout.Source], scale: int }
fn main() {
    let request = jsonDecode[Request](input())
    print(picture.Encode(request.root, request.modules, "main.pxl", request.scale))
}'''


class SelfHostedPictureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        cls.compiler = compile_project(cls.files)[1].bytecode
        cls.driver_files = dict(cls.files, **{'main.pxl': DRIVER})
        cls.encoder = compile_project(cls.driver_files)[1].bytecode

    def encode(self, document, encoder=None, scale=4):
        root = dict(document)
        modules = root.pop('bundle', {'modules': {}})['modules']
        with patch('pixellang.picture.encode_picture', side_effect=AssertionError('host picture')), patch(
            'PIL.Image.Image.save', side_effect=AssertionError('host PNG')):
            output = VM(encoder or self.encoder, **SELFHOST_HEAP_LIMITS,
                        input_text=json.dumps(dict(root=root, modules=modules, scale=scale)),
                        max_steps=100000000, max_output_chars=64000000).run()
        return bytes(output[0])

    def execute(self, document):
        root = dict(document)
        modules = root.pop('bundle')['modules']
        with patch('pixellang.text.parse_text', side_effect=AssertionError('host text')), patch(
            'pixellang.parser.parse', side_effect=AssertionError('host pixel')), patch(
            'pixellang.spatial.analyze', side_effect=AssertionError('host spatial')), patch(
            'pixellang.semantics.Checker.check', side_effect=AssertionError('host check')), patch(
            'pixellang.backend.lower', side_effect=AssertionError('host lower')), patch(
            'pixellang.backend.assemble', side_effect=AssertionError('host assembly')):
            events = VM(self.compiler, **SELFHOST_HEAP_LIMITS, input_text=json.dumps(dict(
                source='program.png', text='', operation='pixel-bytecode-stream', document=root, modules=modules)),
                max_steps=100000000, max_depth=1024).run()
        result = read_bytecode_stream(events)
        self.assertEqual(result['diagnostics'], [])
        return VM(result['bytecode']).run()

    def run_link_driver(self, body):
        files = dict(self.files)
        files['main.pxl'] = ('import "picture.pxl" as picture\n'
                             'import "spatial_layout.pxl" as layout\nfn main() {\n' + body + '\n}')
        code = compile_project(files)[1].bytecode
        return VM(code, **SELFHOST_HEAP_LIMITS, max_steps=100000000).run()

    def test_link_chunks_exceed_generic_json_budget_without_raising_it(self):
        output = self.run_link_driver('''let links: [layout.Link] = []
    var i = 0
    while i < 14000 {
        append(links, layout.Link{kind: "next", from: [i, 0], to: [i + 1, 0]})
        i += 1
    }
    var rejected = false
    try { let oversized = jsonFrom(links) }
    catch error { rejected = error.message == "JSON structure budget exceeded" }
    print(rejected)
    let chunks = picture.PackLinks(links)
    let transported = jsonDecode[[string]](jsonStringify(jsonFrom(chunks)))
    let restored = picture.UnpackLinks(transported)
    assert(len(restored) == len(links))
    for index, link in restored {
        assert(link.kind == links[index].kind && link.from[0] == index && link.from[1] == 0 && link.to[0] == index + 1 && link.to[1] == 0)
    }
    print(len(restored))''')
        self.assertEqual(output, [True, 14000])

    def test_link_chunks_reject_malformed_and_oversized_payloads(self):
        output = self.run_link_driver('''let invalid = ["", "0", "zz", "0000"]
    for chunk in invalid {
        var rejected = false
        try { let links = picture.UnpackLinks([chunk]) }
        catch error { rejected = true }
        print(rejected)
    }
    let oversized = arrayRepeat(["00"], 1955)
    try { let links = picture.UnpackLinks(oversized); print(false) }
    catch error { print(error.message == "Picture exceeds link chunk limit") }
    print(len(picture.UnpackLinks(picture.PackLinks([]))))''')
        self.assertEqual(output, [True, True, True, True, True, 0])

    def test_link_chunks_enforce_expansion_count_and_zlib_boundaries(self):
        link = dict(kind="next", **{"from": [0, 0], "to": [1, 0]})
        payloads = [
            zlib.compress(b" " * 100001),
            zlib.compress(json.dumps([link] * 513).encode()),
            zlib.compress(b"[]"),
            zlib.compress(b"{}"),
            zlib.compress(json.dumps([link]).encode()) + b"trailing",
        ]
        invalid = json.dumps([data.hex() for data in payloads])
        output = self.run_link_driver('let invalid = ' + invalid + '''
    for chunk in invalid {
        var rejected = false
        try { let links = picture.UnpackLinks([chunk]) }
        catch error { rejected = true }
        print(rejected)
    }''')
        self.assertEqual(output, [True] * len(payloads))

    def test_picture_pixels_survive_metadata_free_resave_and_execute(self):
        source = compile_project({'main.pxl': 'import "lib.pxl" as lib\nfn main() { print(lib.twice(21)) }',
                                  'lib.pxl': 'export fn twice(n: int) -> int = n * 2'})[0]
        data = self.encode(source)
        with Image.open(io.BytesIO(data)) as image:
            decoded = decode_picture(image)
            self.assertEqual(image.info, {})
            fresh = Image.frombytes('RGBA', image.size, image.tobytes())
            stream = io.BytesIO()
            fresh.save(stream, format='PNG')
        with Image.open(io.BytesIO(stream.getvalue())) as image:
            self.assertEqual(decode_picture(image), decoded)
        self.assertEqual(self.execute(decoded), [42])

    def test_bulk_array_builtins_survive_semantic_pixels(self):
        document = compile_project({'main.pxl': 'fn main() { let a = arrayRepeat([1, 2], 2); extend(a, a); print(a) }'})[0]
        with Image.open(io.BytesIO(self.encode(document))) as image:
            decoded = decode_picture(image)
        self.assertEqual(self.execute(decoded), [[1, 2, 1, 2, 1, 2, 1, 2]])

    def test_pixellang_compiles_picture_encoder(self):
        # Only pass reachable encoder modules to avoid unrelated compiler source roots.
        files = {name: self.driver_files[name] for name in (
            'main.pxl', 'picture.pxl', 'picture_colors.pxl', 'picture_layout.pxl', 'png.pxl', 'spatial_layout.pxl', 'pixel_decode.pxl', 'pixel_tokens.pxl')}
        encoder, _ = compile_with(self.compiler, files)
        source = compile_project({'main.pxl': 'fn main() { print(42) }'})[0]
        with Image.open(io.BytesIO(self.encode(source, encoder))) as image:
            decoded = decode_picture(image)
        self.assertEqual(self.execute(decoded), [42])

    def load_file(self, path, access):
        with patch('PIL.Image.open', side_effect=AssertionError('host PNG reader')), patch(
            'pixellang.picture.decode_picture', side_effect=AssertionError('host picture reader')), patch(
            'pixellang.text.parse_text', side_effect=AssertionError('host text')), patch(
            'pixellang.parser.parse', side_effect=AssertionError('host pixel')), patch(
            'pixellang.spatial.analyze', side_effect=AssertionError('host spatial')), patch(
            'pixellang.semantics.Checker.check', side_effect=AssertionError('host check')), patch(
            'pixellang.backend.lower', side_effect=AssertionError('host lower')), patch(
            'pixellang.backend.assemble', side_effect=AssertionError('host assembly')):
            events = VM(self.compiler, **SELFHOST_HEAP_LIMITS, file_access=access,
                        input_text=json.dumps(dict(source=path, text='', operation='png-file-bytecode-stream', file=path)),
                        max_steps=100000000, max_depth=1024).run()
        return read_bytecode_stream(events)

    def test_native_picture_file_compilation_and_replay(self):
        import tempfile
        from pixellang.fileaccess import FileAccess
        source = compile_project({'main.pxl': 'import "lib.pxl" as lib\nfn main() { print(lib.twice(21)) }',
                                  'lib.pxl': 'export fn twice(n: int) -> int = n * 2'})[0]
        data = self.encode(source)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'program.png').write_bytes(data)
            access = FileAccess(read_root=directory)
            result = self.load_file('program.png', access)
            self.assertEqual(result['diagnostics'], [])
            self.assertEqual(VM(result['bytecode']).run(), [42])
            recorded = access.document()
        self.assertEqual(self.load_file('program.png', FileAccess.restore(recorded)), result)

    def test_native_picture_failure_diagnostics(self):
        import tempfile
        from pixellang.fileaccess import FileAccess
        from pixellang.picture import encode_picture
        source = compile_project({'main.pxl': 'fn main() { print(42) }'})[0]
        image = encode_picture(source, scale=4)
        # Only fixture construction uses the independent host image implementation.
        import struct
        import zlib
        from pixellang.picture import pixel_bytes
        prefix = pixel_bytes(image, 20)
        size = struct.unpack('>I', prefix[8:12])[0]
        header = json.loads(zlib.decompress(pixel_bytes(image, 20 + size)[20:]))
        left, top = header['modules'][0]['origin']
        variants = []
        broken = image.copy()
        broken.putpixel((0, 0), (0, 0, 0, 255))
        variants.append((broken, 'signature'))
        broken = image.copy()
        broken.putpixel((left + 1, top + 1), (1, 2, 3, 255))
        variants.append((broken, 'Nonuniform'))
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'bad.png').write_bytes(b'not a PNG')
            result = self.load_file('bad.png', FileAccess(read_root=directory))
            self.assertEqual(result['diagnostics'][0]['phase'], 'image')
            self.assertFalse(result['bytecode']['functions'])
            for broken, message in variants:
                broken.save(Path(directory, 'bad.png'), format='PNG')
                result = self.load_file('bad.png', FileAccess(read_root=directory))
                self.assertIn(message, result['diagnostics'][0]['message'])
                self.assertFalse(result['bytecode']['functions'])
            result = self.load_file('bad.png', FileAccess())
            self.assertEqual(result['diagnostics'][0]['phase'], 'io')
            self.assertFalse(result['bytecode']['functions'])

    def test_pixellang_compiles_picture_reader(self):
        from pixellang.picture import encode_picture
        files = dict(self.files)
        files['main.pxl'] = '''import "picture_reader.pxl" as reader
fn main() {
    let document = reader.Decode(jsonDecode[[int]](input()), 1000000)
    print(document.root)
    print(document.modules)
}'''
        reader, _ = compile_with(self.compiler, files)
        source = compile_project({'main.pxl': 'fn main() { print(42) }'})[0]
        buffer = io.BytesIO()
        encode_picture(source, scale=4).save(buffer, format='PNG')
        with patch('PIL.Image.open', side_effect=AssertionError('host image')), patch(
            'pixellang.picture.decode_picture', side_effect=AssertionError('host picture')):
            output = VM(reader, **SELFHOST_HEAP_LIMITS, input_text=json.dumps(list(buffer.getvalue())),
                        max_steps=100000000, max_depth=1024).run()
        decoded = dict(output[0], bundle=dict(version='0.8', entry='main.pxl', modules=output[1]))
        self.assertEqual(self.execute(decoded), [42])

    def test_text_to_png_file_to_execution_uses_only_pixellang_frontends(self):
        import tempfile
        from pixellang.fileaccess import FileAccess
        files = {'main.pxl': 'import "lib.pxl" as lib\nfn main() { print(lib.twice(21)) }',
                 'lib.pxl': 'export fn twice(n: int) -> int = n * 2'}
        with tempfile.TemporaryDirectory() as directory:
            access = FileAccess(read_root=directory, write_root=directory)
            with patch('pixellang.text.parse_text', side_effect=AssertionError('host text')), patch(
                'pixellang.semantics.Checker.check', side_effect=AssertionError('host checker')), patch(
                'pixellang.picture.encode_picture', side_effect=AssertionError('host picture')), patch(
                'PIL.Image.Image.save', side_effect=AssertionError('host PNG encoder')):
                output = VM(self.compiler, **SELFHOST_HEAP_LIMITS, file_access=access,
                            input_text=json.dumps(dict(source='main.pxl', text='', files=files,
                                                       operation='png-file', file='program.png', scale=4)),
                            max_steps=100000000, max_depth=1024).run()[0]
            self.assertEqual(output['diagnostics'], [])
            self.assertEqual(output['bytes'], Path(directory, 'program.png').stat().st_size)
            result = self.load_file('program.png', FileAccess(read_root=directory))
            self.assertEqual(result['diagnostics'], [])
            self.assertEqual(VM(result['bytecode']).run(), [42])

    def test_png_delivery_failure_preserves_existing_file(self):
        import tempfile
        from pixellang.fileaccess import FileAccess
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'program.png')
            path.write_bytes(b'existing')
            for text, scale in (('fn main() { print(missing) }', 4), ('fn main() { print(42) }', 3)):
                output = VM(self.compiler, **SELFHOST_HEAP_LIMITS,
                            file_access=FileAccess(write_root=directory),
                            input_text=json.dumps(dict(source='main.pxl', text='', files={'main.pxl': text},
                                                       operation='png-file', file='program.png', scale=scale)),
                            max_steps=100000000, max_depth=1024).run()[0]
                self.assertTrue(output['diagnostics'])
                self.assertEqual(output['bytes'], 0)
                self.assertEqual(path.read_bytes(), b'existing')
