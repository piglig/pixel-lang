"""Native semantic pixels publish immutable regions and preserve permissions."""
import json
from pathlib import Path
import re
import unittest

from pixellang.codec import validate
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

DRIVER = '''import "pixel_ast.pxl" as pixels
import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
record Request { files: map[string, string], mode: string }
effect fn RunTest()->Result[int,Error] {
 let request = (jsonDecode::[Request](input()))?
 let checked = checker.Check(types.Analyze(bindings.Bind(project.Build(request.files, "main.pxl"))))
 if request.mode == "mapped" { print(jsonFrom(pixels.BuildMapped(checked))) }
 else if request.mode == "named" { print(jsonFrom(pixels.BuildNamed(checked))) }
 else { print(jsonFrom(pixels.Build(checked))) }

 return .Ok(0)
}

effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
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

class ImmutablePixelEmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bytecode = compile_native_sources(sources())

    def encode(self, source, mode='plain'):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        vm = VM(self.bytecode, input_text=json.dumps(dict(files=files, mode=mode)),
                max_steps=100_000_000, max_depth=1024, max_heap_items=16_000_000,
                max_heap_objects=1_000_000, max_output_chars=64_000_000)
        return json.loads(vm.run()[0])

    def values(self, document):
        return [token.value for token in validate(document)]

    def test_value_types_and_scoped_access_survive_emission(self):
        result = self.encode('fn Copy[T](value: T) -> T { return value }\nfn add(values: inout [int]) { values.append(2) }\neffect fn main() { var values: [int] = [1]; let view = Copy(values); add(&values); print(view); print(values) }')
        self.assertEqual(result['diagnostics'], [])
        values = self.values(result['source'])
        self.assertIn('inout', values)
        for retired in ('mut', 'Frozen', 'freeze', 'thaw'):
            self.assertNotIn(retired, values)

    def test_metadata_modes_keep_identical_semantic_geometry(self):
        source = 'record Box { values: [int] }\neffect fn main() { var box = Box{values: [1]}; box.values.append(2); print(box.values) }'
        results = [self.encode(source, mode) for mode in ('plain', 'named', 'mapped')]
        for result in results:
            self.assertEqual(result['diagnostics'], [])
            self.assertEqual(result['source']['pixels'], results[0]['source']['pixels'])
            self.assertEqual(result['source']['spatial'], results[0]['source']['spatial'])
        self.assertEqual(results[0]['source']['metadata'], {})
        self.assertNotIn('source_map', results[1]['source']['metadata'])
        self.assertEqual(results[2]['source']['metadata']['filename'], 'main.pxl')
        self.assertTrue(results[2]['source']['metadata']['symbols'])
        self.assertEqual(len(results[2]['source']['metadata']['source_map']), len(results[2]['source']['pixels']))

    def test_multiple_aliases_publish_one_dependency_document(self):
        files = {'main.pxl': 'import "lib.pxl" as a\nimport "lib.pxl" as b\neffect fn main(){print(a.value());print(b.value())}',
                 'lib.pxl': 'export fn value() -> int { return 7 }'}
        result = self.encode(files)
        self.assertEqual(result['diagnostics'], [])
        document = result['source']
        self.assertEqual(len(document['bundle']['modules']), 1)
        self.assertEqual(self.values(document).count('import'), 1)
        for module in document['bundle']['modules'].values():
            self.assertTrue(validate(module))
        self.assertEqual(self.encode(files), result)

    def test_control_flow_closure_and_flow_regions_remain_explicit(self):
        source = 'flow Counter(seed: int) step(delta: int) -> int { state total: int = seed; next total = delta; output total }\neffect fn main() { let counter = Counter(1); var count = 0; let callback = fn() { return count }; while count < 2 { count += 1; if count == 1 { continue }; print(callback()) }; let result: Result[int, Error] = .Ok(counter.step(2)); match result { .Ok(value)=>{print(value)}, .Err(error)=>{print(error.message)} } }'
        result = self.encode(source)
        self.assertEqual(result['diagnostics'], [])
        document = result['source']
        values = self.values(document)
        for marker in ('flow', 'state', 'next', 'output', 'lambda_body', 'while', 'continue', 'match'):
            self.assertIn(marker, values)
        # Inspect native region links independently of the host spatial parser.
        children = [link for link in document['spatial']['links'] if link['kind'] == 'child']
        self.assertTrue(children)
        positions = {tuple(pixel['position']) for pixel in document['pixels']}
        self.assertTrue(all(tuple(link['from']) in positions and tuple(link['to']) in positions for link in children))

    def test_invalid_readonly_write_publishes_no_partial_document(self):
        result = self.encode('fn main() { let values = [1]; values.append(2) }', 'mapped')
        self.assertTrue(result['diagnostics'])
        self.assertEqual(result['source']['pixels'], [])
        self.assertEqual(result['source']['bundle']['modules'], {})
