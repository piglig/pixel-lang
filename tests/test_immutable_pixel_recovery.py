"""Value-preserving native pixel recovery, independent of the CLI transport."""
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from pixellang.bootstrap import read_bytecode_stream
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources
from tests.test_immutable_pixel_emission import DRIVER as EMITTER

RECOVER = '''import "pixel_document.pxl" as document
effect fn RunTest()->Result[int,Error] { let value=(jsonParse(input()))?; print(jsonFrom(document.Load(value, "fixture.pixel")))
 return .Ok(0)
}

effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
'''
COMPILE = '''import "assembler.pxl" as assembler
import "bytecode_stream.pxl" as stream
import "ir.pxl" as ir
import "closures.pxl" as closures
import "instances.pxl" as instances
import "checker.pxl" as checker
import "types.pxl" as types
import "model.pxl" as model
import "bindings.pxl" as bindings
record Request { project: model.Project, operation: string }
effect fn RunTest()->Result[int,Error] {
 let request = (jsonDecode::[Request](input()))?
 var planned = closures.Build(instances.Build(checker.Check(types.Analyze(bindings.Bind(request.project)))))
 let lowered = ir.Build(&planned)
 if request.operation == "ir" { print(jsonFrom(lowered)) } else { stream.Emit(assembler.Assemble(lowered)) }

 return .Ok(0)
}

effect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}
'''

def sources(driver):
    root = Path(__file__).resolve().parents[1] / 'selfhost'
    files = {'main.pxl': driver}
    def add(name):
        if name in files:
            return
        files[name] = (root / name).read_text()
        for child in re.findall(r'^import "([^"]+)"', files[name], re.M):
            add(child)
    for name in re.findall(r'^import "([^"]+)"', driver, re.M):
        add(name)
    return files

class ImmutablePixelRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.emitter = compile_native_sources(sources(EMITTER))
        cls.recoverer = compile_native_sources(sources(RECOVER))
        cls.compiler = compile_native_sources(sources(COMPILE))

    def invoke(self, code, request):
        with patch('pixellang.text.parse_text', side_effect=AssertionError('host parser')), \
             patch('pixellang.spatial.analyze', side_effect=AssertionError('host spatial recovery')):
            return VM(code, input_text=json.dumps(request), max_steps=100_000_000,
                      max_depth=1024, max_heap_items=16_000_000, max_heap_objects=1_000_000,
                      max_output_chars=64_000_000).run()

    def emit(self, source):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        emitted = json.loads(self.invoke(self.emitter, dict(files=files, mode='plain'))[0])
        self.assertEqual(emitted['diagnostics'], [])
        return emitted['source']

    def recover(self, document):
        return json.loads(self.invoke(self.recoverer, document)[0])

    def check_roundtrip(self, source, expected):
        project = self.recover(self.emit(source))
        self.assertEqual(project['diagnostics'], [])
        result = read_bytecode_stream(self.invoke(self.compiler, dict(project=project, operation='bytecode')))
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), expected)

    def test_generic_snapshot_and_array_value(self):
        self.check_roundtrip('fn Copy[T](value: T) -> T { return value }\neffect fn main(){ var a: [int] = [1]; let snapshot = Copy(a); a.append(2); var b = snapshot; b.append(3); print(a); print(snapshot); print(b) }', [[1, 2], [1], [1, 3]])

    def test_deep_record_snapshot(self):
        self.check_roundtrip('record Box { values: [int] }\neffect fn main(){ var b = Box{values: [1]}; let view = b; b.values.append(2); print(view.values); print(b.values) }', [[1], [1, 2]])

    def test_map_value_snapshot(self):
        self.check_roundtrip('effect fn main(){ var m = map[string,int]{"a":1}; let view = m; m["a"] = 2; print(view["a"]); print(m["a"]) }', [1, 2])

    def test_option_payload_is_a_value(self):
        self.check_roundtrip('''effect fn main() {
 var values: [int] = [1]
 let present: Option[[int]] = .Some(values)
 let snapshot = present
 values.append(3)
 match present { .Some(items) => { var copy=items; copy.append(2); print(copy) }, .None => {} }
 match snapshot { .Some(items) => { print(items) }, .None => {} }
 print(values)
}''', [[1, 2], [1], [1, 3]])

    def test_flow_and_closure_scopes(self):
        self.check_roundtrip('flow Counter(seed: int) step(delta: int) -> int { state total: int = seed; next total = delta; output total }\neffect fn main() { let counter = Counter(1); var count = 0; let callback = fn() { return count }; while count < 2 { count += 1; if count == 1 { continue }; print(callback()) }; print(counter.step(2)) }', [0, 1])

    def test_imported_nominal_value(self):
        self.check_roundtrip({'main.pxl': 'import "data.pxl" as d\neffect fn main(){ var b = d.Box{values:[1]}; b.values.append(2); print(b.values) }', 'data.pxl': 'export record Box { values: [int] }'}, [[1, 2]])

    def test_enum_payload_snapshot(self):
        self.check_roundtrip('''enum Choice { Values(items: [int]) }
effect fn main() {
 let value: Choice = .Values([1])
 let snapshot = value
 match value { .Values(items) => { var copy=items; copy.append(2); print(copy) } }
 match snapshot { .Values(items) => { print(items) } }
}''', [[1, 2], [1]])

    def test_recovered_pattern_binding_cannot_be_mutated(self):
        project = self.recover(self.emit('''enum Choice { Values(items: [int]) }
effect fn main() {
 let value: Choice = .Values([1])
 match value { .Values(items) => { var copy=items; copy.append(2) } }
}'''))
        self.assertEqual(project['diagnostics'], [])
        nodes = project['modules'][0]['syntax']['nodes']
        # Plain pixels retain binding identity, not the original source spelling.
        methods = [node for node in nodes if node['kind']=='field' and node['text']=='append']
        bindings = [node for node in nodes if node['kind']=='binding']
        self.assertEqual(len(methods),1)
        self.assertEqual(len(bindings),1)
        receiver = nodes[methods[0]['children'][0]]
        self.assertEqual(receiver['kind'],'name')
        self.assertNotEqual(receiver['text'],bindings[0]['text'])
        # Retarget the recovered mutation to the readonly pattern binding.
        # The checker must reject it even though the source pixel document was valid.
        receiver['text']=bindings[0]['text']
        result = read_bytecode_stream(self.invoke(self.compiler, dict(project=project, operation='bytecode')))
        self.assertTrue(any('readonly binding' in d['message'] for d in result['diagnostics']),result['diagnostics'])
        self.assertFalse(result['bytecode']['functions'])

    def test_pattern_still_requires_matching_nominal_enum(self):
        project = self.recover(self.emit('''enum First { Value }
enum Second { Value }
effect fn main() {
 match First.Value { First.Value => { print(1) } }
 match Second.Value { Second.Value => { print(2) } }
}'''))
        self.assertEqual(project['diagnostics'], [])
        nodes = project['modules'][0]['syntax']['nodes']
        patterns = [node for node in nodes if node['kind'] == 'variant_pattern']
        self.assertEqual(len(patterns), 2)
        patterns[0]['children'][0] = patterns[1]['children'][0]
        result = read_bytecode_stream(self.invoke(self.compiler, dict(project=project, operation='bytecode')))
        self.assertTrue(any('Pattern type does not match subject' in d['message']
                            for d in result['diagnostics']))
        self.assertFalse(result['bytecode']['functions'])

    def test_missing_module_publishes_no_partial_project(self):
        document = self.emit({'main.pxl': 'import "lib.pxl" as lib\neffect fn main(){print(lib.value())}', 'lib.pxl': 'export fn value() -> int { return 7 }'})
        document['bundle']['modules'] = {}
        project = self.recover(document)
        self.assertTrue(any('Missing pixel module' in d['message'] for d in project['diagnostics']))
        self.assertEqual(project['modules'], [])

    def test_invalid_document_is_format_diagnostic(self):
        for document in ([], {'magic': 'PIXELLANG'}):
            project = self.recover(document)
            self.assertEqual(project['diagnostics'][0]['phase'], 'format')
            self.assertEqual(project['modules'], [])
