"""AST owner indexing uses ordinary values and preserves input arenas."""
import json
from pathlib import Path
import unittest
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "model.pxl" as model
import "syntax_index.pxl" as index
effect fn main() {
 match jsonDecode::[model.Parsed](input()) {
  .Ok(syntax) => { Dispatch(syntax) },
  .Err(error) => { print(error.message) }
 }
}
effect fn Dispatch(syntax:model.Parsed) {
 print(index.Owners(syntax))
 print(syntax.roots)
}'''


class NativeSyntaxIndexValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = compile_native_sources({'model.pxl':(ROOT/'selfhost/model.pxl').read_text(),
            'syntax_index.pxl':(ROOT/'selfhost/syntax_index.pxl').read_text(),'main.pxl':DRIVER})

    def run_index(self, nodes, roots):
        source=dict(tokens=[], nodes=[dict(kind=kind,text='',children=children,start=0,end=0)
                                     for kind,children in nodes],roots=roots,diagnostics=[])
        return VM(self.code,input_text=json.dumps(source),max_steps=20_000_000).run()

    def test_nested_callable_owner_chains_and_lambda_inheritance(self):
        nodes=[('export',[1]),('fn',[2]),('block',[3,5]),('lambda',[4]),('name',[]),
               ('fn',[6]),('name',[]),('flow',[8]),('name',[])]
        self.assertEqual(self.run_index(nodes,[0,7]),[[-1,-1,1,1,1,1,5,-1,7],[0,7]])

    def test_empty_arena_and_large_shared_input_arrays(self):
        self.assertEqual(self.run_index([],[]),[[],[]])
        nodes=[('fn',list(range(1,4097))),*[('name',[]) for _ in range(4096)]]
        self.assertEqual(self.run_index(nodes,[0]),[[-1]+[0]*4096,[0]])
