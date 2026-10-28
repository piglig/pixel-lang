"""Frame queries relocate slots and concrete types across declaration edits."""
import re
from pathlib import Path
import unittest

from tests.native_compiler import compile_native_sources, native_driver

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "compiler_session.pxl" as session
import "frame_cache.pxl" as cache
import "frames.pxl" as frames
import "instances.pxl" as instances
import "closures.pxl" as closures
record Row { hits:int, checked:int, size:int }
effect fn main() {
    match jsonDecode::[[map[string,string]]](input()) {
        .Ok(requests) => { Run(requests) }, .Err(error) => { print(error); assert(false) }
    }
}
effect fn Run(requests:[map[string,string]]) {
    var state = session.Empty()
    var snapshot = cache.Empty()
    for files in requests {
        let checked = session.Check(files, "main.pxl", 2000000, &state)
        assert(len(checked.checked.diagnostics) == 0)
        var planned = closures.Build(instances.Build(checked.checked))
        var fresh = planned
        var query = cache.Begin(snapshot, state.semantic, planned)
        let actual = frames.BuildCached(&planned, &query)
        let expected = frames.Build(&fresh)
        assert(len(actual.diagnostics) == 0)
        assert(jsonStringify(jsonFrom(actual)) == jsonStringify(jsonFrom(expected)))
        assert(jsonStringify(jsonFrom(planned.named.checked.resolved.types)) == jsonStringify(jsonFrom(fresh.named.checked.resolved.types)))
        snapshot = query.current
        print(Row{hits:query.hits, checked:query.checked, size:cache.Size(snapshot)})
    }
}
'''


class FrameQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {'main.pxl': DRIVER}
        pending = re.findall(r'import "([^"]+)"', DRIVER)
        while pending:
            name = pending.pop()
            if name in files:
                continue
            files[name] = (ROOT / 'selfhost' / name).read_text()
            pending.extend(re.findall(r'import "([^"]+)"', files[name]))
        cls.code = compile_native_sources(files)
        cls.execute = staticmethod(cls.enterClassContext(native_driver(cls.code)))

    def run_edits(self, *files):
        return self.execute(list(files), steps=100_000_000)

    def test_one_body_edit_only_replans_its_frame(self):
        first = {'main.pxl': 'fn Value()->int=1\neffect fn main(){print(Value())}'}
        changed = {'main.pxl': first['main.pxl'].replace('=1', '=2')}
        rows = self.run_edits(first, first, changed, changed)
        self.assertEqual([(r['hits'], r['checked']) for r in rows], [(0, 2), (2, 0), (1, 1), (2, 0)])
        self.assertGreater(rows[-1]['size'], 0)

    def test_arena_reordering_relocates_nominal_types_and_symbols(self):
        first = {'main.pxl': 'import "data.pxl" as data\nfn Read(value:data.Item)->int { let copy=value; return copy.value }\neffect fn main(){print(Read(data.Item{value:42}))}',
                 'data.pxl': 'export record Item { value:int }'}
        changed = dict(first, **{'data.pxl': 'record Earlier { other:string }\n' + first['data.pxl']})
        rows = self.run_edits(first, first, changed)
        self.assertEqual((rows[-1]['hits'], rows[-1]['checked']), (2, 0))

    def test_generic_closure_instances_keep_separate_slot_types(self):
        source = 'fn Make[T](value:T)->fn()->T { let saved=value; return fn()=saved }\neffect fn main(){print(Make(1)());print(Make("x")())}'
        first = {'main.pxl': source}
        changed = {'main.pxl': source.replace('let saved=value;', 'let extra=value; let saved=extra;')}
        rows = self.run_edits(first, first, changed, changed)
        self.assertEqual((rows[1]['hits'], rows[1]['checked']), (5, 0))
        self.assertEqual((rows[2]['hits'], rows[2]['checked']), (1, 4))
        self.assertEqual((rows[3]['hits'], rows[3]['checked']), (5, 0))

    def test_changed_dependency_invalidates_inferred_local_type(self):
        first = {'main.pxl': 'import "data.pxl" as data\nfn Read(){let value=data.Value()}\neffect fn main(){Read()}',
                 'data.pxl': 'export fn Value()->int=1'}
        changed = dict(first, **{'data.pxl': 'export fn Value()->string="x"'})
        rows = self.run_edits(first, first, changed, changed)
        self.assertGreaterEqual(rows[2]['checked'], 2)
        self.assertEqual(rows[3]['checked'], 0)
