"""Inout parameter permissions cannot escape through lexical capture."""
import json
from pathlib import Path
import unittest
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT=Path(__file__).resolve().parents[1]

class InoutBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files={name:(ROOT/'selfhost'/name).read_text() for name in
               ('model.pxl','lexer.pxl','parser.pxl','project.pxl','bindings.pxl')}
        files['main.pxl']='''import "project.pxl" as project
import "bindings.pxl" as bindings
effect fn main() { print(jsonFrom(bindings.Bind(project.Build(map[string,string]{"main.pxl":input()},"main.pxl")))) }
'''
        cls.code=compile_native_sources(files)

    def bind(self, source):
        return json.loads(VM(self.code,input_text=source,max_steps=5000000).run()[0])

    def test_only_inout_parameters_allow_rebinding(self):
        bound=self.bind('fn change(x: inout int, y: int) { x=2 }\nfn main() {}')
        self.assertEqual(bound['diagnostics'], [])
        params={s['name']:s for s in bound['symbols'] if s['kind']=='parameter'}
        self.assertTrue(params['x']['mutable'])
        self.assertFalse(params['y']['mutable'])
        self.assertTrue(self.bind('fn change(x: int) { x=2 }\nfn main() {}')['diagnostics'])

    def test_direct_and_transitive_captures_are_rejected(self):
        for body in ['let f=fn() -> int = x',
                     'let f=fn() { x=2 }',
                     'let f=fn() -> fn() -> int = fn() -> int = x']:
            with self.subTest(body=body):
                result=self.bind('fn change(x: inout int) { '+body+' }\nfn main() {}')
                self.assertIn('inout parameters cannot be captured',str(result['diagnostics']))

    def test_copied_value_can_be_captured(self):
        result=self.bind('fn change(x: inout int) { let copy=x; let f=fn() -> int = copy }\nfn main() {}')
        self.assertEqual(result['diagnostics'], [])
