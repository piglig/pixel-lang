import json
from pathlib import Path
import tempfile
import time
import unittest

from pixellang.compiler_client import CompilerClient
from pixellang.image_transport import read_modules, read_parsed, stage_parsed
from tests.image_transport_runtime import Execution, execute

ROOT=Path(__file__).resolve().parents[1]


class ImageTransportTests(unittest.TestCase):
    def test_large_unicode_arena_round_trip_through_bounded_files_and_stream(self):
        from tests.native_compiler import load_native_compiler
        code=load_native_compiler(driver='import "image_transport.pxl" as t\neffect fn main(){match t.LoadParsed("parsed-0.json") { .Ok(parsed)=>{t.EmitParsed(parsed)}, .Err(error)=>{print(error.message)} }}')
        value=dict(syntax=dict(tokens=[],nodes=[dict(kind='string',text='汉字🙂'*20,
            children=[],start=i,end=i+1) for i in range(12000)], roots=[],diagnostics=[]), entries=[],diagnostics=[])
        self.assertGreater(len(json.dumps(value)),1000000)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);stage_parsed(p,'0',value)
            self.assertGreater(len(list(p.glob('nodes-*.json'))),1)
            self.assertTrue(all(f.stat().st_size<1000000 for f in p.glob('*.json')))
            output,_=execute(Execution(code),{},p,time.monotonic()+120)
            self.assertTrue(all(len(item)<1000000 for item in output))
            self.assertEqual(read_parsed(output),value)

    def test_stream_truncation_and_counts_are_rejected(self):
        header=dict(kind='parsed',roots=[],entries=[],diagnostics=[],syntaxDiagnostics=[])
        for records in ([header],[header,dict(kind='parsed-end',first=1,second=0)],
                        [header,dict(kind='parsed-end',first=0,second=0),header]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                read_parsed([json.dumps(x) for x in records])
        with self.assertRaises(ValueError):
            read_modules([json.dumps(dict(kind='module',id='0',source=dict(pixels=[])))])

    def test_reused_execution_resets_heap_output_and_effect_state(self):
        with CompilerClient() as compiler:
            code=compiler.request('compile', files={'main.pxl':'effect fn main(){var a:[int]=[1];a.append(2);print(a);print(input())}'})['result']['bytecode']
        session=Execution(code)
        with tempfile.TemporaryDirectory() as root:
            first,_=execute(session,{'run':1},root,time.monotonic()+10)
            session.vm.cancel()
            second,_=execute(session,{'run':2},root,time.monotonic()+10)
            self.assertEqual(first,[[1,2],json.dumps({'run':1})])
            self.assertEqual(second,[[1,2],json.dumps({'run':2})])
            with self.assertRaisesRegex(ValueError,'bounded VM input'):
                execute(session,{'large':'x'*1000000},root,time.monotonic()+10)

    def test_native_service_rejects_corrupt_tiles_and_recovers_for_next_request(self):
        import io
        import struct
        import zlib
        from PIL import Image
        from pixellang.picture import pixel_bytes
        from pixellang.model import PixelError
        from pixellang.vm import VM

        with CompilerClient() as compiler:
            image = compiler.request('export-png', files={
                'main.pxl': 'effect fn main(){print(42)}'}, scale=4)['result']['image']
            with Image.open(io.BytesIO(image)) as opened:
                edited = opened.convert('RGBA')
            size = struct.unpack('>I', pixel_bytes(edited, 20)[8:12])[0]
            header = json.loads(zlib.decompress(pixel_bytes(edited, 20 + size)[20:]))
            left, top = header['modules'][0]['origin']
            for dy in (1, 2):
                for dx in (1, 2):
                    edited.putpixel((left + dx, top + dy), (1, 2, 3, 255))
            corrupted = io.BytesIO()
            edited.save(corrupted, format='PNG')
            for invalid in (b'not a png', corrupted.getvalue()):
                with self.subTest(bytes=len(invalid)), self.assertRaises(PixelError):
                    compiler.request('compile-png', image=invalid)
            result = compiler.request('compile-png', image=image)
            self.assertEqual(VM(result['result']['bytecode']).run(), [42])
