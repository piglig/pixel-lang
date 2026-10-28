"""Checker buffers publish values and carry writes through callback boundaries."""
from pathlib import Path
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('model.pxl', 'types.pxl', 'checker_state.pxl')
DRIVER = '''import "model.pxl" as model
import "checker_state.pxl" as state
fn saveExpression(module:int, node:int, typeId:int, c:inout state.State) -> int = state.Save(module,node,typeId,&c)
fn visit(callback:fn(int,int,int,inout state.State)->int, c:inout state.State) {
    callback(0,3,7,&c)
    callback(0,4,8,&c)
}
effect fn main() {
    var c = state.State{loopDepth: 0,valueDepth:0, returnTypes:[],activeValues:[],functionOwners:[],calls:[],references:map[string,int]{},declarations:map[string,int]{},annotations:map[string,int]{},signatures:map[string,int]{},expressions:[],diagnostics:[]}
    c.references[state.Key(0,3)] = 12
    c.annotations[state.Key(0,4)] = 8
    let before = c
    visit(saveExpression,&c)
    c.activeValues.append(12)
    c.functionOwners.append(9)
    c.references[state.Key(0,3)] = 24
    c.diagnostics.append(model.Diagnostic{source:"test",phase:"check",message:"bad",start:0,end:1,line:1,column:1})
    let published = c
    c.expressions[0] = model.NodeType{module:0,node:3,typeId:99}
    c.activeValues.pop()
    c.functionOwners.pop()
    assert(len(before.expressions)==0)
    assert(state.Reference(before,0,3)==12)
    assert(len(before.diagnostics)==0)
    assert(len(published.expressions)==2)
    assert(published.expressions[0].typeId==7)
    assert(published.expressions[1].typeId==8)
    assert(len(published.activeValues)==1)
    assert(len(published.functionOwners)==1)
    assert(len(c.activeValues)==0)
    assert(len(c.functionOwners)==0)
    assert(state.Reference(c,0,3)==24)
    assert(state.Reference(c,0,10)==-1)
    assert(state.Annotation(c,0,4)==8)
    assert(state.Declared(c,0,4)==-1)
    print(true)
}
'''


class NativeCheckerStateValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT / 'selfhost' / name).read_text() for name in MODULES}
        cls.code = compile_native_sources({**files, 'main.pxl': DRIVER})

    def test_callback_writes_and_published_snapshots_are_independent(self):
        self.assertEqual(VM(self.code).run(), [True])
