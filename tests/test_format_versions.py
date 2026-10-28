"""The 0.9 data contract has one accepted version, without compatibility paths."""
import json
import unittest
import zlib

from PIL import Image
from pixellang.codec import new_source, rgba, validate
from pixellang.model import VERSIONS, PixelError
from pixellang.picture import MAGIC, encode_picture, decode_picture
from pixellang.temporal import Timeline

class FormatVersionTests(unittest.TestCase):
    def source(self):
        value=new_source(2,1,[dict(position=[0,0],rgba=rgba('int',1)),dict(position=[1,0],rgba=rgba('int',2))])
        value['spatial']['links']=[dict(kind='next',**{'from':[0,0],'to':[1,0]})]
        return value

    def test_current_picture_uses_chunked_links_and_current_signature(self):
        source=self.source();image=encode_picture(source,scale=4)
        pixels=image.convert('RGBA').tobytes()
        def read(n):return bytes(pixels[(i//3)*4+i%3] for i in range(n))
        self.assertEqual(read(8),b'PXIMG\x09\r\n')
        self.assertEqual(MAGIC,read(8))
        size=int.from_bytes(read(20)[8:12],'big')
        header=json.loads(zlib.decompress(read(20+size)[20:]))
        self.assertEqual(header['version'],'0.9')
        spec=header['modules'][0]['source']
        self.assertEqual(spec['spatial'],{'links':[]})
        self.assertTrue(spec['linkChunks'])
        restored=decode_picture(image)
        self.assertEqual(restored['bundle']['version'],'0.9')
        restored.pop('bundle')
        self.assertEqual(restored,source)

    def test_each_old_source_axis_and_bundle_are_rejected(self):
        self.assertEqual(set(VERSIONS.values()),{'0.9'})
        for axis in VERSIONS:
            source=self.source();source['versions'][axis]='0.8'
            with self.subTest(axis=axis),self.assertRaises(PixelError):validate(source)
        source=self.source();source['bundle']=dict(version='0.8',entry='main.pxl',modules={})
        with self.assertRaises(PixelError):validate(source)

    def test_previous_image_signature_is_rejected(self):
        image=encode_picture(self.source(),scale=4).convert('RGBA');pixels=bytearray(image.tobytes())
        pixels[(5//3)*4+5%3]=8
        changed=Image.frombytes('RGBA',image.size,bytes(pixels))
        with self.assertRaisesRegex(PixelError,'signature'):decode_picture(changed)

    def test_previous_timeline_version_is_rejected_before_recompilation(self):
        with self.assertRaisesRegex(PixelError,'Unsupported timeline'):
            Timeline.restore(dict(format='pixellang-time',version='0.8'))
