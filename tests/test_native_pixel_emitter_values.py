"""Emission buffers and published regions have independent value ownership."""
from pathlib import Path
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('model.pxl', 'types.pxl', 'pixel_tokens.pxl', 'spatial_layout.pxl',
           'pixel_ast_state.pxl', 'literal_pixels.pxl', 'failures.pxl')
DRIVER = '''import "pixel_ast_state.pxl" as state
import "literal_pixels.pxl" as literals
import "model.pxl" as model
effect fn main() {
 var c = state.Context{module:0,regions:[],origins:[],references:map[string,int]{},declarations:map[string,int]{},expressions:map[string,int]{},calls:map[string,int]{},annotations:map[string,int]{},symbolIds:map[string,int]{},tokenIndices:map[int,map[string,int]]{},bodies:[],imports:map[string,bool]{},diagnostics:[]}
 var colors = literals.Integer(42)
 let row = state.Row(colors,-1,7,&c)
 assert(row==0)
 let published = c
 for color in literals.Boolean(true) { colors.append(color) }
 assert(len(c.regions[0].colors)==1)
 let second = state.Row(colors,0,9,&c)
 assert(second==1)
 c.regions[0].colors[0][0]=99
 c.origins[0]=8
 c.diagnostics.append(model.Diagnostic{source:"test",phase:"pixels",message:"bad",start:0,end:1,line:1,column:1})
 assert(len(published.regions)==1)
 assert(published.regions[0].colors[0][0]==16)
 assert(published.origins[0]==7)
 assert(len(published.diagnostics)==0)
 assert(len(c.regions[1].colors)==2)
 assert(c.regions[1].colors[0][0]==16)
 assert(state.Row([],4,10,&c)==4)
 assert(len(c.regions)==2)
 print(literals.Integer(-9223372036854775808))
 match literals.Text("界😀") {.Ok(colors)=>{print(colors)},.Err(error)=>{print(error.message)}}
 print(literals.Float(-0.0))
}
'''


class NativePixelEmitterValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT / 'selfhost' / name).read_text() for name in MODULES}
        cls.code = compile_native_sources({**files, 'main.pxl': DRIVER})

    def test_regions_snapshots_and_literals_preserve_values(self):
        from pixellang.codec import encode_token
        self.assertEqual(VM(self.code).run(), [
            encode_token('int', -2**63), encode_token('str', '界😀'), encode_token('float64', -0.0),
        ])
