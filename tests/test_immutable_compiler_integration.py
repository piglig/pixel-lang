"""Exercise immutable semantics through the actual native compiler main entry."""
import json
import io
from pathlib import Path
import tempfile
import unittest
import zlib

from PIL import Image

# Independent output inspection only. Compiler requests below remain subject to
# the generated-regression guard against calling Pillow or host compiler stages.
inspect_png = Image.open

from pixellang.bootstrap import read_bytecode_stream
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources, native_driver

FILES={'main.pxl': '''import "data.pxl" as d
effect fn main(){
 let value: d.Choice = d.Choice.Values([1])
 let snapshot = value
 match value { d.Choice.Values(items) => {var edited = items; edited.append(2); print(edited)}, d.Choice.Empty => {} }
 match snapshot { d.Choice.Values(items) => {print(items)}, d.Choice.Empty => {} }
}''', 'data.pxl': 'export enum Choice { Empty, Values(items: [int]) }'}

class ImmutableCompilerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parents[1]
        cls.compiler=compile_native_sources({p.name:p.read_text() for p in (root/'selfhost').glob('*.pxl')})
        cls.run_compiler=staticmethod(cls.enterClassContext(native_driver(cls.compiler)))

    def request(self, operation, files=None, directory=None, **extra):
        request=dict(source='main.pxl',text='',operation=operation,files=FILES if files is None else files)
        request.update(extra)
        return self.events(request, directory)

    def events(self, request, directory=None):
        return self.run_compiler(request, root=directory, steps=150_000_000,
                                 output_chars=64_000_000)

    def reference_events(self, request):
        return VM(self.compiler, input_text=json.dumps(request), max_steps=150_000_000,
                  max_depth=1024, max_heap_items=24_000_000,
                  max_heap_objects=1_500_000, max_output_chars=64_000_000).run()

    def test_native_entry_matches_reference_vm(self):
        for source in ('effect fn main(){print(42)}', 'fn main(){let value:int="wrong"}'):
            with self.subTest(source=source):
                request=dict(source='main.pxl',text='',operation='bytecode-stream',
                             files={'main.pxl':source})
                self.assertEqual(read_bytecode_stream(self.events(request)),
                                 read_bytecode_stream(self.reference_events(request)))

    def executable(self, events):
        result=read_bytecode_stream(events)
        self.assertEqual(result['diagnostics'],[])
        self.assertEqual(VM(result['bytecode']).run(),[[1,2],[1]])

    def test_text_and_pixel_entrypoints_preserve_direct_enum_construction(self):
        self.executable(self.request('bytecode-stream'))
        emitted=self.request('pixels')[0]
        self.assertEqual(emitted['diagnostics'],[])
        document=emitted['source'];modules=document.pop('bundle')['modules']
        self.executable(self.request('pixel-bytecode-stream',document=document,modules=modules))

    def test_png_execution_and_source_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            emitted=self.request('studio-png-file',directory=directory,file='program.png',scale=4)[0]
            self.assertEqual(emitted['diagnostics'],[])
            self.assertEqual(emitted['bytes'],(Path(directory)/'program.png').stat().st_size)
            self.executable(self.request('png-file-bytecode-stream',directory=directory,file='program.png',byteLimit=8_000_000,imageLimit=32_000_000))
            recovered=json.loads(self.request('png-file-source',directory=directory,file='program.png',byteLimit=8_000_000,imageLimit=32_000_000)[0])
            self.assertEqual(recovered['diagnostics'],[])
            self.executable(self.request('bytecode-stream',files=recovered['files'],source=recovered['entry']))

    def test_diagnostic_and_analysis_entrypoints_publish_readonly_arenas(self):
        for operation in ['project','bind','types','check','instances','closures','schemas','frames']:
            with self.subTest(operation=operation):
                result=self.request(operation)[0]
                self.assertEqual(result['diagnostics'],[])
        invalid=self.request('bytecode-stream',files={'main.pxl':'effect fn main(){let values=[1];values.append(2)}'})
        result=read_bytecode_stream(invalid)
        self.assertTrue(result['diagnostics']);self.assertFalse(result['bytecode']['functions'])

    def test_document_source_recovery(self):
        emitted=self.request('pixels')[0];self.assertEqual(emitted['diagnostics'],[])
        recovered=json.loads(self.request('pixel-document-source',native=emitted['source'])[0])
        self.assertEqual(recovered['diagnostics'],[])
        self.executable(self.request('bytecode-stream',files=recovered['files'],source=recovered['entry']))

    def test_recovered_patterns_keep_generic_payload_permissions(self):
        files={'main.pxl': '''enum Choice[T] { Value(item: T) }
effect fn main(){
 let value: Choice[[int]] = Choice::[[int]].Value([])
 let option: Option[[int]] = Option.Some([])
 match value {Choice.Value(items) => {var edited=items; edited.append(1); print(edited)}}
 match option {Option.Some(items) => {var edited=items; edited.append(2); print(edited)}, Option.None => {}}
}'''}
        emitted=self.request('pixels',files=files)[0]
        self.assertEqual(emitted['diagnostics'],[])
        recovered=json.loads(self.request('pixel-document-source',native=emitted['source'])[0])
        self.assertEqual(recovered['diagnostics'],[])
        result=read_bytecode_stream(self.request('bytecode-stream',files=recovered['files'],source=recovered['entry']))
        self.assertEqual(result['diagnostics'],[])
        self.assertEqual(VM(result['bytecode']).run(),[[1],[2]])

    def test_studio_bundle_keeps_maps_in_output_and_omits_them_from_png(self):
        with tempfile.TemporaryDirectory() as directory:
            events=self.request('studio-bundle',directory=directory,file='studio.png',scale=4)
            end=next(i for i,event in enumerate(events) if isinstance(event,str) and json.loads(event).get('kind')=='end')
            self.executable(events[:end+1])
            mapped=events[end+2]
            self.assertEqual(mapped['diagnostics'],[])
            self.assertIn('source_map',mapped['source']['metadata'])
            for module in mapped['source']['bundle']['modules'].values():
                self.assertIn('source_map',module['metadata'])
            image=inspect_png(io.BytesIO((Path(directory)/'studio.png').read_bytes())).convert('RGBA')
            pixels=image.tobytes()
            def read(n):return bytes(pixels[(i//3)*4+i%3] for i in range(n))
            size=int.from_bytes(read(20)[8:12],'big')
            header=json.loads(zlib.decompress(read(20+size)[20:]))
            for module in header['modules']:
                self.assertNotIn('source_map',module['source']['metadata'])
                self.assertIn('symbols',module['source']['metadata'])
            self.executable(self.request('png-file-bytecode-stream',directory=directory,file='studio.png',byteLimit=8_000_000,imageLimit=32_000_000))
