"""Native specialization syntax never depends on a symbol-name pre-scan."""
import json
from pathlib import Path
import re
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT=Path(__file__).resolve().parents[1]
DRIVER='''import "parser.pxl" as parser
import "source_format.pxl" as format
import "source_writer.pxl" as writer
import "model.pxl" as model
record Request {text:string,editor:bool}
effect fn main(){
 match jsonDecode::[Request](input()) {
  .Ok(request)=>{Run(request)},
  .Err(error)=>{print(error)}
 }
}
effect fn Run(request:Request){
 var parsed=parser.Parse("main.pxl",request.text)
 if request.editor {parsed=parser.ParseEditor("main.pxl",request.text)}
 print(parsed)
 if len(parsed.diagnostics)>0 {return}
 print(format.Format("main.pxl",request.text))
 let module=model.Module{path:"main.pxl",syntax:parsed,imports:[],declarations:[]}
 let project=model.Project{modules:[module],diagnostics:[],entryName:"main"}
 print(writer.Build(project,map[string,string]{},map[string,string]{}))
}'''


class NativeExplicitSpecializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files={'main.pxl':DRIVER}
        pending=re.findall(r'(?m)^import "([^"]+)"',DRIVER)
        while pending:
            name=pending.pop()
            if name in files:continue
            files[name]=(ROOT/'selfhost'/name).read_text()
            pending.extend(re.findall(r'(?m)^import "([^"]+)"',files[name]))
        cls.code=compile_native_sources(files)

    def outputs(self,text,editor=False):
        return VM(self.code,input_text=json.dumps(dict(text=text,editor=editor)),max_steps=25_000_000,max_output_chars=16_000_000).run()

    def parse(self,text):
        parsed=self.outputs(text)[0]
        self.assertEqual(parsed['diagnostics'],[])
        return parsed

    def test_function_import_and_constructor_specializations_have_explicit_ast(self):
        source='''fn identity[T](value:T)->T=value
record Box[T]{value:T}
fn main(){let first=identity::[int](1);let box:Box[int]=Box::[int]{value:first};let result=library.choose::[Box[int]](box)}'''
        parsed=self.parse(source)
        nodes=parsed['nodes']
        specializations=[node for node in nodes if node['kind']=='specialize']
        self.assertEqual(len(specializations),3)
        self.assertTrue(all('::[' in source[node['start']:node['end']] for node in specializations))
        self.assertEqual([nodes[node['children'][0]]['kind'] for node in specializations],['name','name','field'])

    def test_indexing_is_independent_of_later_type_and_function_names(self):
        for declarations in ('','record Index {value:int}','fn items[T](value:T)->T=value','record items[T]{value:T}'):
            with self.subTest(declarations=declarations):
                parsed=self.parse('fn main(){let value=items[Index]}\n'+declarations)
                self.assertEqual(sum(n['kind']=='index' for n in parsed['nodes']),1)
                self.assertFalse(any(n['kind']=='specialize' for n in parsed['nodes']))

    def test_unknown_names_still_parse_as_explicit_type_arguments(self):
        parsed=self.parse('fn main(){let value=foreign.select::[Unknown,other.Type]()}')
        node=next(n for n in parsed['nodes'] if n['kind']=='specialize')
        self.assertEqual(len(node['children']),3)

    def test_nested_types_and_function_type_arguments(self):
        parsed=self.parse('fn main(){let value=apply::[map[string,[Option[int]]],effect fn(inout [int])->bool](callback)}')
        self.assertEqual(sum(n['kind']=='specialize' for n in parsed['nodes']),1)

    def test_old_brackets_are_indexing_and_multiple_indices_are_rejected(self):
        parsed=self.parse('fn main(){let value=identity[int](1)}')
        self.assertTrue(any(n['kind']=='index' for n in parsed['nodes']))
        self.assertFalse(any(n['kind']=='specialize' for n in parsed['nodes']))
        for source in ('fn main(){let value=identity[int,string](1)}',
                       'fn main(){let value=identity::[]()}',
                       'fn main(){let value=identity::int(1)}'):
            self.assertTrue(self.outputs(source)[0]['diagnostics'])

    def test_format_and_source_recovery_preserve_explicit_specialization(self):
        source='// 星 🛰️\nfn main(){let f=library.choose :: [map[string,[int]]] ;let b=Box::[int]{value:1};let x=items[index]} '
        parsed,formatted,written=self.outputs(source)
        self.assertEqual(parsed['diagnostics'],[])
        self.assertEqual(formatted['diagnostics'],[])
        self.assertEqual(written['diagnostics'],[])
        self.assertIn('choose::[map[string, [int]]]',formatted['text'])
        self.assertIn('// 星 🛰️',formatted['text'])
        for text in (formatted['text'],written['files']['main.pxl']):
            output=self.parse(text)
            self.assertEqual(sum(n['kind']=='specialize' for n in output['nodes']),2)
            self.assertEqual(sum(n['kind']=='index' for n in output['nodes']),1)
        self.assertEqual(self.outputs(formatted['text'])[1],formatted)
        self.assertEqual(self.outputs(written['files']['main.pxl'])[2],written)

    def test_unfinished_specialization_retains_editor_diagnostics(self):
        result=self.outputs('fn main(){let value=choose::[',editor=True)[0]
        self.assertTrue(result['diagnostics'])
        self.assertTrue(any(n['kind']=='fn' and n['text']=='main' for n in result['nodes']))


if __name__=='__main__':unittest.main()
