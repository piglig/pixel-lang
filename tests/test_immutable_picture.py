"""Native picture packing and restoration publish immutable documents."""
import io
import json
import unittest
import zlib
from unittest.mock import patch

from PIL import Image
from pixellang.model import PixelError
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources
from tests.test_immutable_pixel_emission import DRIVER as EMITTER
from tests.test_immutable_pixel_recovery import sources

DRIVER = '''import "picture.pxl" as picture
import "picture_reader.pxl" as reader
import "spatial_layout.pxl" as layout
record Request { root: layout.Source, modules: map[string, layout.Source], scale: int, operation: string, bytes: [int] }
effect fn RunTest()->Result[int,Error] { let r = (jsonDecode::[Request](input()))?; if r.operation == "encode" {print(jsonFrom(picture.Encode(r.root,r.modules,"main.pxl",r.scale)))} else {print(jsonFrom(reader.Decode(r.bytes,64000000)))}
 return .Ok(0)
}

effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
'''

class ImmutablePictureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler=compile_native_sources(sources(DRIVER))
        cls.emitter=compile_native_sources(sources(EMITTER))

    def emit(self, files):
        vm=VM(self.emitter,input_text=json.dumps(dict(files=files,mode='mapped')),max_steps=100_000_000,max_depth=1024,
              max_heap_items=16_000_000,max_heap_objects=1_000_000,max_output_chars=64_000_000)
        result=json.loads(vm.run()[0]);self.assertEqual(result['diagnostics'],[])
        document=result['source'];modules=document.pop('bundle')['modules']
        return document,modules

    def run_picture(self, root, modules, operation, data=(), scale=4):
        with patch('pixellang.picture.encode_picture',side_effect=AssertionError('host encoder')), \
             patch('pixellang.picture.decode_picture',side_effect=AssertionError('host decoder')):
            result=VM(self.compiler,input_text=json.dumps(dict(root=root,modules=modules,operation=operation,bytes=list(data),scale=scale)),
                      max_steps=100_000_000,max_depth=1024,max_heap_items=16_000_000,max_heap_objects=1_000_000,max_output_chars=64_000_000).run()[0]
        result=json.loads(result)
        self.assertIn(result['variant'], ('Ok','Err'))
        if result['variant']=='Err':
            raise PixelError('picture',result['fields']['error']['message'])
        value=result['fields']['value']
        return value if operation!='encode' else bytes(value)

    def assert_source_equal(self, actual, expected):
        # Physical tile scanning has row-major order; semantic links determine
        # traversal. Compare geometry without imposing source serialization order.
        self.assertEqual({k:v for k,v in actual.items() if k!='pixels'}, {k:v for k,v in expected.items() if k!='pixels'})
        self.assertEqual(sorted(actual['pixels'],key=lambda p:p['position']),sorted(expected['pixels'],key=lambda p:p['position']))

    def test_named_multimodule_native_roundtrip(self):
        root,modules=self.emit({'main.pxl':'import "data.pxl" as data\neffect fn main(){print(data.value())}', 'data.pxl':'export fn value() -> int { return 7 }'})
        before=json.dumps([root,modules],sort_keys=True)
        data=self.run_picture(root,modules,'encode')
        self.assertEqual(data[:8],b'\x89PNG\r\n\x1a\n')
        decoded=self.run_picture(root,modules,'decode',data)
        self.assert_source_equal(decoded['root'],root)
        self.assertEqual(set(decoded['modules']),set(modules))
        for name in modules:self.assert_source_equal(decoded['modules'][name],modules[name])
        self.assertEqual(decoded['entry'],'main.pxl')
        self.assertEqual(json.dumps([root,modules],sort_keys=True),before)

    def test_invalid_scale_and_corrupt_png_are_rejected(self):
        root,modules=self.emit({'main.pxl':'effect fn main(){print(1)}'})
        with self.assertRaises(PixelError):self.run_picture(root,modules,'encode',scale=3)
        data=bytearray(self.run_picture(root,modules,'encode'));data[-1]^=1
        with self.assertRaises(PixelError):self.run_picture(root,modules,'decode',data)

    def test_legacy_container_version_is_rejected(self):
        root,modules=self.emit({'main.pxl':'effect fn main(){print(1)}'})
        data=self.run_picture(root,modules,'encode')
        image=Image.open(io.BytesIO(data)).convert('RGBA');pixels=bytearray(image.tobytes())
        def read(n):return bytes(pixels[(i//3)*4+i%3] for i in range(n))
        prefix=read(20);size=int.from_bytes(prefix[8:12],'big')
        header=json.loads(zlib.decompress(read(20+size)[20:]));header['version']='0.8'
        raw=json.dumps(header,separators=(',',':')).encode();packed=zlib.compress(raw)
        payload=prefix[:8]+len(packed).to_bytes(4,'big')+len(raw).to_bytes(4,'big')+zlib.crc32(packed).to_bytes(4,'big')+packed
        self.assertLessEqual((len(payload)+2)//3,image.width*min(m['origin'][1] for m in header['modules']))
        for i,b in enumerate(payload):pixels[(i//3)*4+i%3]=b
        changed=Image.frombytes('RGBA',image.size,bytes(pixels));out=io.BytesIO();changed.save(out,format='PNG')
        with self.assertRaisesRegex(PixelError,'Unsupported picture container'):
            self.run_picture(root,modules,'decode',out.getvalue())
