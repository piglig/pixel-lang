"""Semantic image documents own ordinary metadata and geometry values."""
from pathlib import Path
import unittest

from tests.native_compiler import compile_native_sources
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]


class NativeLayoutValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = compile_native_sources({
            'failures.pxl': (ROOT / 'selfhost/failures.pxl').read_text(),
            'spatial_layout.pxl': (ROOT / 'selfhost/spatial_layout.pxl').read_text(),
            'main.pxl': '''import "spatial_layout.pxl" as layout
fn inspect(original: layout.Source) {
 var edited = original
 edited.metadata["filename"] = jsonFrom("edited.pxl")
 edited.pixels[0].position[0] = 9
 edited.spatial.links[0].to[0] = 8
 assert(!original.metadata.has("filename"))
 assert(original.pixels[0].position[0] == 0)
 assert(original.spatial.links[0].to[0] != 8)
 assert(edited.pixels[0].position[0] == 9)
 assert(edited.spatial.links[0].to[0] == 8)
 let published = edited
 edited.metadata.remove("filename")
 assert(published.metadata.has("filename"))
 assert(!edited.metadata.has("filename"))
}
effect fn main() {
 let regions = [layout.Region{colors: [[64, 0, 0, 255], [64, 0, 1, 255]], parent: -1}]
 match layout.Build(regions, 1, false) { .Ok(value)=>{inspect(value)}, .Err(error)=>{print(error.message)} }
 match layout.Compact(regions) { .Ok(value)=>{inspect(value)}, .Err(error)=>{print(error.message)} }
 print("isolated")
}
''',
        })

    def test_metadata_pixels_and_links_preserve_published_documents(self):
        self.assertEqual(VM(self.code).run(), ['isolated'])
