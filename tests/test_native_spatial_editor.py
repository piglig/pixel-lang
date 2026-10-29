"""Spatial properties use native member schemas throughout editor operations."""
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from pixellang.native_editor import NativeSymbolIndex
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "project.pxl" as project
import "editor_index.pxl" as editor
import "model.pxl" as model
effect fn main(){
 match jsonDecode::[map[string,string]](input()) {
  .Ok(files)=>{print(jsonFrom(editor.Analyze(project.BuildEditorParsed(files,"main.pxl",map[string,model.Parsed]{},["main.pxl"]))))},
  .Err(error)=>{print(error)}
 }
}'''


class NativeSpatialEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sources = {'main.pxl': DRIVER}
        pending = re.findall(r'(?m)^import "([^"]+)"', DRIVER)
        while pending:
            name = pending.pop()
            if name in sources:
                continue
            sources[name] = (ROOT / 'selfhost' / name).read_text()
            pending.extend(re.findall(r'(?m)^import "([^"]+)"', sources[name]))
        cls.code = compile_native_sources(sources)

    def index(self, files):
        with patch('pixellang.text.parse_text', side_effect=AssertionError('host parser forbidden')):
            output = VM(self.code, input_text=json.dumps(files), max_steps=40_000_000,
                        max_output_chars=16_000_000, **SELFHOST_HEAP_LIMITS).run()
        return NativeSymbolIndex(files, 'main.pxl', json.loads(output[0]))

    def complete(self, text):
        offset = text.index('|')
        files = {'main.pxl': text.replace('|', '', 1)}
        return self.index(files).complete('main.pxl', offset, analyze=self.index)

    def test_properties_preserve_frame_and_payload_types(self):
        prefix = '''frame World = meters
record Payload {count:int}
fn inspect(entity:Entity[World,Payload],edge:Link[World,Payload,string]){
'''
        for receiver, expected in (
            ('entity', {'id': 'EntityId[World, Payload]', 'position': 'Point3[World]', 'payload': 'Payload'}),
            ('edge', {'id': 'LinkId[World, Payload, string]', 'source': 'EntityId[World, Payload]', 'target': 'EntityId[World, Payload]', 'payload': 'string'}),
            ('entity.payload', {'count': 'int'}),
            ('entity.position', {'x': 'float64', 'y': 'float64', 'z': 'float64'}),
        ):
            with self.subTest(receiver=receiver):
                items = self.complete(prefix + receiver + '.|}\nfn main(){}')
                self.assertEqual({item['label']: item['detail'] for item in items if item['kind'] == 'Field'}, expected)
                self.assertTrue(all(item['kind'] in ('Field', 'Method') for item in items))

    def test_property_hover_and_payload_field_navigation(self):
        text = '''frame World = meters
record Payload {count:int}
fn inspect(entity:Entity[World,Payload])->int=entity.payload.count
fn main(){}
'''
        index = self.index({'main.pxl': text})
        self.assertEqual(index.diagnostics, [])
        self.assertEqual(index.hover('main.pxl', text.index('.payload') + 1)['label'],
                         'payload: Payload')
        definition = index.definition('main.pxl', text.index('.count') + 1)
        self.assertEqual(definition['start']['offset'], text.index('count:int'))

    def test_space_method_completion_uses_typed_native_contracts(self):
        text='frame World=meters\nrecord Cost {weight:int}\nfn inspect(scene:Space[World,string,Cost]){scene.|}\nfn main(){}'
        items=self.complete(text)
        self.assertEqual(len(items),20)
        self.assertTrue(all(item['kind']=='Method' for item in items))
        details={item['label']:item['detail'] for item in items}
        self.assertEqual(details['insert'],'fn insert(key: string, position: Point3[World], payload: string) -> Space[World, string, Cost]')
        self.assertEqual(details['connect'],'fn connect(key: string, source: EntityId[World, string], target: EntityId[World, string], payload: Cost) -> Space[World, string, Cost]')
        self.assertEqual(details['get'],'fn get(id: EntityId[World, string]) -> Option[Entity[World, string]]')
        self.assertEqual(details['neighbors'],'fn neighbors(entity: EntityId[World, string], radius: float64) -> [Entity[World, string]]')

    def test_geometry_method_completion_preserves_frame_relations(self):
        text='frame World=meters\nframe Local=centimeters\nfn inspect(transform:Transform3[World,Local]){transform.|}\nfn main(){}'
        details={item['label']:item['detail'] for item in self.complete(text)}
        self.assertEqual(details,{
            'rotation':'[[float64]]',
            'translation':'Vec3[Local]',
            'point':'fn point(value: Point3[World]) -> Point3[Local]',
            'vector':'fn vector(value: Vec3[World]) -> Vec3[Local]',
            'then':'fn then[Target](after: Transform3[Local, Target]) -> Transform3[World, Target]',
        })
        text='frame World=meters\nfn inspect(point:Point3[World]){point.di|}\nfn main(){}'
        self.assertEqual(self.complete(text),[dict(label='distance',kind='Method',detail='fn distance(other: Point3[World]) -> float64')])

    def test_method_hover_and_unfinished_call_signature(self):
        prefix='frame World=meters\nrecord Cost {weight:int}\nfn inspect(scene:Space[World,string,Cost],origin:EntityId[World,string]){'
        text=prefix+'let nearby=scene.neighbors(origin,2.0)}\nfn main(){}'
        index=self.index({'main.pxl':text})
        self.assertEqual(index.diagnostics,[])
        self.assertEqual(index.hover('main.pxl',text.index('neighbors'))['label'],
                         'fn neighbors(entity: EntityId[World, string], radius: float64) -> [Entity[World, string]]')
        text=prefix+'scene.insert("sample", Point3[World]{x:0.0,y:0.0,z:0.0}, '
        signature=self.index({'main.pxl':text}).signature_help('main.pxl',len(text))
        self.assertIsNotNone(signature)
        self.assertEqual(signature['parameters'],['key: string','position: Point3[World]','payload: string'])
        self.assertEqual(signature['activeParameter'],2)



if __name__ == '__main__':
    unittest.main()
