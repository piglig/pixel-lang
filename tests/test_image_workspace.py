import json
from pathlib import Path
import tempfile
import unittest

from pixellang.bootstrap import normalized
from pixellang.image_workspace import DRIVER, cached_parse, compile_image, digest
from pixellang.picture import encode_picture
from pixellang.project import compile_project
from pixellang.vm import VM
from pixellang.model import PixelError

ROOT=Path(__file__).resolve().parents[1]


class ImageWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory()
        cls.base=Path(cls.folder.name)
        sources={p.name:p.read_text() for p in (ROOT/'selfhost').glob('*.pxl')}
        cls.code=compile_project(dict(sources, **{'main.pxl':DRIVER}))[1].bytecode
        cls.compiler=cls.base/'worker.json'
        cls.compiler.write_text(normalized(cls.code))

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def fixture(self, path, factor=2):
        doc=compile_project({'main.pxl':'import "lib.pxl" as lib\nfn main(){print(lib.twice(21))}',
            'lib.pxl':f'export fn twice(n:int)->int=n*{factor}'})[0]
        encode_picture(doc,scale=4).save(path)

    def run_image(self, image, output, **kwargs):
        return compile_image(image,output,compiler=self.compiler,timeout=90,**kwargs)

    def test_parallel_cache_reuse_and_module_local_invalidation(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder); image=p/'input.png';cache=p/'cache';self.fixture(image)
            cold=self.run_image(image,p/'cold',workers=1,cache=cache)
            warm=self.run_image(image,p/'warm',workers=2,cache=cache)
            self.assertEqual(cold['cacheHits'],0)
            self.assertEqual(warm['cacheHits'],2)
            self.assertEqual(len(warm['workers']),2)
            self.assertEqual((p/'cold/compiler.json').read_bytes(),(p/'warm/compiler.json').read_bytes())
            self.fixture(image,3)
            changed=self.run_image(image,p/'changed',workers=2,cache=cache)
            self.assertEqual(changed['cacheHits'],1)
            code=json.loads((p/'changed/compiler.json').read_text())
            self.assertEqual(VM(code).run(),[63])
            uncached=self.run_image(image,p/'uncached',workers=2)
            self.assertFalse(uncached['cacheEnabled'])
            self.assertEqual(uncached['cacheHits'],0)
            self.assertEqual((p/'changed/compiler.json').read_bytes(),(p/'uncached/compiler.json').read_bytes())
            different=dict(self.code,constants=self.code['constants']+self.code['constants'][:1])
            replacement=p/'new-worker.json'; replacement.write_text(normalized(different))
            invalidated=compile_image(image,p/'new-environment',workers=2,cache=cache,
                compiler=replacement,timeout=90)
            self.assertEqual(invalidated['cacheHits'],0)
            self.assertEqual((p/'changed/compiler.json').read_bytes(),(p/'new-environment/compiler.json').read_bytes())

    def test_invalid_cache_payload_is_a_miss(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);key='a'*64
            (p/(key+'.json')).write_text('broken')
            self.assertIsNone(cached_parse(p,key))
            parsed=dict(diagnostics=[],syntax={},entries=[])
            (p/(key+'.json')).write_text(json.dumps(dict(key=key,parsed=parsed,sha256='wrong')))
            self.assertIsNone(cached_parse(p,key))
            (p/(key+'.json')).write_text(json.dumps(dict(key='b'*64,parsed=parsed,sha256=digest(parsed))))
            self.assertIsNone(cached_parse(p,key))

    def test_deadline_and_corrupt_png_never_emit_successful_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder); image=p/'input.png';self.fixture(image)
            with self.assertRaises(TimeoutError):
                compile_image(image,p/'timeout',compiler=self.compiler,timeout=1e-9)
            self.assertFalse(json.loads((p/'timeout/summary.json').read_text())['verified'])
            self.assertFalse((p/'timeout/compiler.json').exists())
            image.write_bytes(b'not a png')
            with self.assertRaisesRegex(PixelError,'PNG'):
                self.run_image(image,p/'bad',workers=2)
            self.assertFalse(json.loads((p/'bad/summary.json').read_text())['verified'])
            self.assertFalse((p/'bad/compiler.json').exists())

    def test_worker_rejects_corrupt_tiles_after_successful_header_index(self):
        import struct
        import zlib
        from PIL import Image
        from pixellang.picture import pixel_bytes
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);image=p/'input.png';self.fixture(image)
            with Image.open(image) as opened:
                edited=opened.convert('RGBA')
            size=struct.unpack('>I',pixel_bytes(edited,20)[8:12])[0]
            header=json.loads(zlib.decompress(pixel_bytes(edited,20+size)[20:]))
            left,top=header['modules'][0]['origin']
            for dy in (1,2):
                for dx in (1,2):
                    edited.putpixel((left+dx,top+dy),(1,2,3,255))
            edited.save(image)
            with self.assertRaisesRegex(RuntimeError,'worker failed'):
                self.run_image(image,p/'failed-worker',workers=2)
            summary=json.loads((p/'failed-worker/summary.json').read_text())
            self.assertIn('index',summary)
            self.assertFalse(summary['verified'])
            self.assertFalse((p/'failed-worker/compiler.json').exists())
