"""Semantic pixel parsing preserves access modes only in their proper positions."""
import json
from pathlib import Path
import re
import unittest
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT=Path(__file__).resolve().parents[1]

class InoutPixelParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files={}
        def add(name):
            if name in files: return
            files[name]=(ROOT/'selfhost'/name).read_text()
            for child in re.findall(r'^import "([^"]+)"',files[name],re.M): add(child)
        for name in ('pixel_reader.pxl','pixel_types.pxl','pixel_expressions.pxl'): add(name)
        files['main.pxl']='''import "pixel_reader.pxl" as reader
import "pixel_decode.pxl" as decoder
import "pixel_types.pxl" as types
import "pixel_expressions.pxl" as expressions
record Request { mode:string,tokens:[decoder.Token] }
effect fn main() {
 match jsonDecode::[Request](input()) { .Ok(request)=>{Dispatch(request)}, .Err(error)=>{print(error.message)} }
}
effect fn Dispatch(request:Request) {
 var r=reader.New("test.png",0,request.tokens)
 if request.mode=="parameter" { types.Parameter(&r) }
 else if request.mode=="type" { types.Parse(&r) }
 else { expressions.Parse(&r) }
 if r.index!=len(r.tokens) { reader.Problem("Unconsumed access-mode pixels", &r) }
 print(jsonFrom(r.diagnostics)); print(jsonFrom(r.nodes))
}
'''
        cls.code=compile_native_sources(files)

    def parse(self, mode, *tokens):
        request=dict(mode=mode,tokens=[dict(kind=k,value=v,position=[i,0]) for i,(k,v) in enumerate(tokens)])
        result=VM(self.code,input_text=json.dumps(request),max_steps=5000000).run()
        return json.loads(result[0]),json.loads(result[1])

    def test_parameter_and_function_type_modes_are_explicit_nodes(self):
        mode=[('type','inout'),('punct','('),('type','int'),('punct',')')]
        errors,nodes=self.parse('parameter',*mode)
        self.assertEqual(errors,[])
        self.assertEqual(nodes[-1]['kind'],'type_inout')
        errors,nodes=self.parse('type',('type','function'),('punct','('),*mode,
            ('punct',')'),('punct','->'),('punct','('),('type','unit'),('punct',')'))
        self.assertEqual(errors,[])
        self.assertEqual(nodes[-1]['kind'],'type_fn')
        self.assertEqual(nodes[nodes[-1]['children'][0]]['kind'],'type_inout')
        errors,_=self.parse('type',*mode)
        self.assertTrue(errors)
        errors,_=self.parse('parameter',('type','inout'),('punct','('),*mode,('punct',')'))
        self.assertTrue(errors)

    def test_argument_marker_is_not_a_general_reference_expression(self):
        errors,nodes=self.parse('expression',('id',1),('punct','('),('punct','&'),('id',2),('punct',')'))
        self.assertEqual(errors,[])
        self.assertEqual(nodes[nodes[-1]['children'][1]]['kind'],'inout_argument')
        errors,_=self.parse('expression',('punct','&'),('id',2))
        self.assertTrue(errors)
        errors,_=self.parse('expression',('id',1),('punct','('),('punct','&'),('id',2),('punct','('),('punct',')'),('punct',')'))
        self.assertTrue(errors)

    def test_retired_permission_pixels_are_rejected_at_their_origin(self):
        for name in ('mut', 'Frozen'):
            for mode in ('type', 'parameter'):
                with self.subTest(name=name, mode=mode):
                    errors,nodes=self.parse(mode,('type',name),('punct','('),
                        ('type','int'),('punct',')'))
                    self.assertEqual(errors[0]['position'], [0,0])
                    self.assertIn('Unsupported type pixel',errors[0]['message'])
                    self.assertFalse(any(n['kind']=='type_mutable' for n in nodes))
