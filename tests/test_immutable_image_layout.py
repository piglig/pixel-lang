"""Image construction uses private buffers and immutable published documents."""
import json
from pathlib import Path
import re
import unittest

from pixellang.vm import VM
from pixellang.model import PixelError
from pixellang.codec import validate
from pixellang.spatial import analyze
from tests.native_compiler import compile_native_sources

DRIVER = '''import "spatial_layout.pxl" as layout
import "debug_info.pxl" as debug
import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "instances.pxl" as instances
import "closures.pxl" as closures
record Request { operation: string, files: map[string, string], regions: [layout.Region], spacing: int, reverse: bool }
record Pair { before: layout.Source, after: layout.Source }
effect fn main() {
 match Run() { .Ok(value)=>{print(value)}, .Err(error)=>{print(jsonFrom(error))} }
}
effect fn Run()->Result[json,Error] {
 let request = jsonDecode::[Request](input())?
 if request.operation == "layout" { return .Ok(jsonFrom(layout.Build(request.regions, request.spacing, request.reverse)?)) }
 if request.operation == "compact" { return .Ok(jsonFrom(layout.Compact(request.regions)?)) }
 let sources = project.Build(request.files, "main.pxl")
 if request.operation == "map" {
  let original = layout.Compact(request.regions)?
  var draft = original
  let source = sources.modules[0]
  debug.MapDocument(source, &draft, request.regions, arrayRepeat([source.syntax.roots[0]], len(request.regions)))
  return .Ok(jsonFrom(Pair{before: original, after: draft}))
 }
 var planned = closures.Build(instances.Build(checker.Check(types.Analyze(bindings.Bind(sources)))))
 assert(len(planned.diagnostics) == 0)
 return .Ok(jsonFrom(debug.Build(&planned)))
}
'''

def sources():
    root = Path(__file__).resolve().parents[1] / 'selfhost'
    files = {'main.pxl': DRIVER}
    def add(name):
        if name in files:
            return
        files[name] = (root / name).read_text()
        for child in re.findall(r'^import "([^"]+)"', files[name], re.M):
            add(child)
    for name in re.findall(r'^import "([^"]+)"', DRIVER, re.M):
        add(name)
    return files

class ImmutableImageLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bytecode = compile_native_sources(sources())

    def query(self, operation, regions=(), spacing=1, reverse=False, source='fn main() {}'):
        request = dict(operation=operation, files={'main.pxl': source}, regions=list(regions), spacing=spacing, reverse=reverse)
        return json.loads(VM(self.bytecode, input_text=json.dumps(request), max_steps=30_000_000,
                            max_depth=1024, max_heap_items=16_000_000, max_heap_objects=1_000_000,
                            max_output_chars=64_000_000).run()[0])

    def test_spacing_and_reverse_keep_explicit_region_parents(self):
        from tests.test_selfhost_spatial_layout import region
        regions = [region(-1, 'fn'), region(0, 'if', True), region(1, 'print', 1), region(0, 'return')]
        for spacing, reverse in [(1, False), (7, True)]:
            document = self.query('layout', regions, spacing, reverse)
            parsed = analyze(validate(document), document['spatial']['links'])
            self.assertEqual([r.parent for r in parsed], [None, 0, 1, 0])

    def test_compact_layout_preserves_regions_and_pixel_bounds(self):
        from tests.test_selfhost_spatial_layout import region
        regions = [region(-1, 'fn'), region(0, 'print', '(', 1, '+', 2, ')'), region(0, 'return')]
        document = self.query('compact', regions)
        parsed = analyze(validate(document), document['spatial']['links'])
        self.assertEqual([r.parent for r in parsed], [None, 0, 0])
        width, height = document['dimensions']
        self.assertTrue(all(0 <= p['position'][0] < width and 0 <= p['position'][1] < height for p in document['pixels']))
        self.assertEqual(self.query('compact')['dimensions'], [1, 1])

    def test_debug_mapping_keeps_original_document_unchanged(self):
        from tests.test_selfhost_spatial_layout import region
        source = 'effect fn main() { print(42) }'
        result = self.query('map', [region(-1, 'fn'), region(0, 'print', 42)], source=source)
        self.assertEqual(result['before']['metadata'], {})
        self.assertEqual(result['before']['pixels'], result['after']['pixels'])
        metadata = result['after']['metadata']
        self.assertEqual(metadata['filename'], 'main.pxl')
        self.assertEqual(len(metadata['source_map']), len(result['after']['pixels']))
        self.assertTrue(all(s['source'] == 'main.pxl' and s['start']['offset'] == 0 for s in metadata['source_map'].values()))

    def test_debug_names_cover_generic_functions_and_closures(self):
        result = self.query('debug', source='fn identity[T](value: T) -> T = value\neffect fn main() { let captured = 7; let callback = fn() = captured; print(identity(callback())) }')
        functions = list(result['functions'].values())
        identity = next(f for f in functions if f['name'] == 'identity')
        self.assertEqual(identity['typeArguments'], ['int'])
        self.assertIn('value', identity['names'])
        self.assertTrue(any(f['name'] == 'lambda' and 'captured' in f['names'] for f in functions))

    def test_flow_initializer_names_preserve_constructor_inputs(self):
        result = self.query('debug', source='flow Counter(seed: int) step(delta: int) -> int { state total: int = seed; next total = delta; output total }\neffect fn main() { let counter = Counter(2); print(counter.step(1)) }')
        initial = [f for key, f in result['functions'].items() if key.startswith('$flow:')]
        self.assertTrue(initial)
        self.assertTrue(any(f['names'] == ['seed'] and f['name'] == 'Counter.initial.total' for f in initial))

    def test_invalid_layout_is_rejected(self):
        for regions, spacing, message in [
            ([{'parent': 0, 'colors': [[17, 0, 1, 255]]}], 1, 'earlier region'),
            ([{'parent': -1, 'colors': [[17, 0, 1, 0]]}], 1, 'opaque'),
            ([], 0, 'spacing'),
        ]:
            with self.subTest(message=message):
                result=self.query('layout', regions, spacing)
                self.assertIn(message,result['message'])
                self.assertEqual(result['kind'],'compiler')
