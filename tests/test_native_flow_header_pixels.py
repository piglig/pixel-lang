"""Explicit creation/step separation survives the native semantic-pixel reader."""
import json
from pathlib import Path
import re
import unittest

from pixellang.codec import new_source, rgba
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "pixel_decode.pxl" as decoder
import "pixel_regions.pxl" as regions
import "pixel_statements.pxl" as statements
import "spatial_layout.pxl" as layout
effect fn main(){
 match jsonDecode::[layout.Source](input()) {
  .Ok(source)=>{print(statements.Parse(regions.Analyze(decoder.Decode(source,"flow.pixel"),source.spatial.links,"flow.pixel"),"flow.pixel",0))},
  .Err(error)=>{print(error)}
 }
}'''

class NativeFlowHeaderPixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {'main.pxl': DRIVER}
        pending = re.findall(r'(?m)^import "([^"]+)"', DRIVER)
        while pending:
            name = pending.pop()
            if name in files:
                continue
            files[name] = (ROOT / 'selfhost' / name).read_text()
            pending.extend(re.findall(r'(?m)^import "([^"]+)"', files[name]))
        cls.code = compile_native_sources(files)

    def parse(self, initial=False, marker=True):
        header = [('keyword','flow'),('id',0),('punct','(')]
        if initial:
            header += [('id',1),('punct',':'),('type','int')]
        header += [('punct',')')]
        if marker:
            header += [('keyword','step')]
        header += [('punct','('),('id',2),('punct',':'),('type','int'),('punct',')'),('punct','->'),('type','int')]
        pixels = [dict(position=[i,0],rgba=rgba(kind,value)) for i,(kind,value) in enumerate(header)]
        pixels += [dict(position=[2,1],rgba=rgba('keyword','output')),dict(position=[3,1],rgba=rgba('id',2))]
        document = new_source(len(header),2,pixels,[
            dict(kind='region', **{'from':[0,0], 'to':[0,0]}),
            dict(kind='region', **{'from':[2,1], 'to':[2,1]}),
            dict(kind='child', **{'from':[0,0], 'to':[2,1]}),
        ])
        return VM(self.code,input_text=json.dumps(document),max_steps=20_000_000,max_output_chars=8_000_000).run()[0]

    def test_empty_and_typed_creation_lists_keep_distinct_parameter_kinds(self):
        for initial in (False,True):
            with self.subTest(initial=initial):
                result = self.parse(initial)
                self.assertEqual(result['diagnostics'],[])
                nodes = result['syntax']['nodes']
                parameters = [(node['kind'],node['text']) for node in nodes if node['kind'] in ('initial_parameter','parameter')]
                expected = [('initial_parameter','_pixel_1')] if initial else []
                self.assertEqual(parameters,expected+[('parameter','_pixel_2')])
                self.assertTrue(any(node['kind']=='flow_output' for node in nodes))

    def test_missing_step_marker_is_rejected(self):
        self.assertTrue(self.parse(initial=True,marker=False)['diagnostics'])

if __name__ == '__main__':
    unittest.main()
