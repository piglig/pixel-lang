"""Native cache relocation preserves value types and owned cache state."""
import json
from pathlib import Path
import re
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

DRIVER = '''import "ir_cache.pxl" as cache
import "ir_state.pxl" as state
record Request { previous: cache.Snapshot, stamps: map[string, cache.Stamp], nominal: map[string, string] }
record Output { function: Option[state.Function], hits: int, relocated: int }
effect fn main() {
 match jsonDecode::[Request](input()) {.Ok(request)=>{Run(request)},.Err(error)=>{print(error)}}
}
effect fn Run(request: Request) {
 var reuse = cache.Begin(request.previous, "test", request.stamps)
 reuse.current.targets["3"] = "function"
 reuse.oldFunctions["function"] = "0"
 reuse.newNominal = request.nominal
 reuse.newModules["lib.pxl"] = 2
 let before = reuse
 let function = cache.Take(3, "lib.pxl", &reuse)
 assert(before.hits == 0)
 assert(before.relocated == 0)
 print(jsonFrom(Output{function, hits: reuse.hits, relocated: reuse.relocated}))
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
    add('ir_cache.pxl')
    return files


class SelfHostedIRCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bytecode = compile_native_sources(sources())

    def relocate(self, *, stamps=None, nominal=None, extra=()):
        span = dict(source='lib.pxl', kind='text', points=[[0, 0]], min=[0, 0], max=[0, 0],
                    start=dict(line=1, column=1, offset=0), end=dict(line=1, column=1, offset=0))
        instructions = [dict(op=op, arg=arg, module=0, node=1, span=span) for op, arg in (
            ('RESULT_DECODE', {'result':'Result<@0:1,Error>'}), ('ARRAY', {'type': '@0:1[]', 'count': 0}),
            ('CONST', {'type': 'str', 'value': 'Box@0:1'}), *extra)]
        function = dict(params=[], captures=[], locals=0, result='unit', instructions=instructions)
        stamp = lambda value: dict(tokens=[], contracts={'lib.pxl':[value]})
        previous = dict(contracts={}, frames={'contracts': {}, 'entries': {}}, flows={}, environment='test', stamps={'lib.pxl': stamp('unchanged')}, modules=['lib.pxl'],
                        nominal={'@0:1': 'box'}, targets={'0': 'function'}, functions={'0':function}, queries={}, locations={})
        request = dict(previous=previous, stamps={'lib.pxl': stamp('unchanged')} if stamps is None else {key:stamp(value) for key,value in stamps.items()},
                       nominal={'box': '@2:9'} if nominal is None else nominal)
        result = VM(self.bytecode, input_text=json.dumps(request), max_steps=5_000_000).run()[0]
        return json.loads(result)

    def test_value_operands_relocate_without_rewriting_string_constants(self):
        result = self.relocate()
        self.assertEqual(result['hits'], 1)
        self.assertEqual(result['relocated'], 1)
        self.assertIsNotNone(result['function'])
        function = result['function']
        self.assertEqual([i['module'] for i in function['instructions']], [2, 2, 2])
        self.assertEqual([i['arg'] for i in function['instructions']], [
            {'result':'Result<@2:9,Error>'}, {'type': '@2:9[]', 'count': 0}, {'type': 'str', 'value': 'Box@0:1'}])

    def test_value_capture_and_collection_result_types_relocate(self):
        result = self.relocate(extra=(
            ('CAPTURE_VALUE', {'slot': 0, 'type': 'Box@0:1'}),
            ('VALUE_COLLECTION', {'method': 'pop', 'result': 'Option<Box@0:1>'}),
        ))
        self.assertEqual(result['hits'], 1)
        self.assertEqual(result['relocated'], 1)
        self.assertEqual([item['arg'] for item in result['function']['instructions'][-2:]], [
            {'slot': 0, 'type': 'Box@2:9'},
            {'method': 'pop', 'result': 'Option<Box@2:9>'},
        ])

    def test_missing_type_identity_discards_cached_function(self):
        result = self.relocate(nominal={})
        self.assertIsNone(result['function'])
        self.assertEqual(result['hits'], 0)

    def test_propagation_relocates_both_success_and_error_types(self):
        result = self.relocate(extra=(
            ('PROPAGATE', {'source': 'Result<@0:1[],@0:1>',
                           'target': 'Result<Option<@0:1>,@0:1>'}),
            ('PROPAGATE', {'source': 'Option<@0:1>', 'target': 'Option<@0:1[]>'}),
        ))
        self.assertEqual(result['hits'], 1)
        self.assertEqual(result['relocated'], 1)
        self.assertEqual([item['arg'] for item in result['function']['instructions'][-2:]], [
            {'source': 'Result<@2:9[],@2:9>', 'target': 'Result<Option<@2:9>,@2:9>'},
            {'source': 'Option<@2:9>', 'target': 'Option<@2:9[]>'},
        ])

    def test_checked_operations_relocate_result_without_changing_operation(self):
        result = self.relocate(extra=(
            ('RESULT_DECODE', {'result': 'Result<@0:1[],Error>'}),
            ('RESULT_BUILTIN', {'name': 'readText', 'result': 'Result<str,Error>'}),
        ))
        self.assertEqual(result['hits'], 1)
        self.assertEqual(result['relocated'], 1)
        self.assertEqual([item['arg'] for item in result['function']['instructions'][-2:]], [
            {'result': 'Result<@2:9[],Error>'},
            {'name': 'readText', 'result': 'Result<str,Error>'},
        ])

    def test_changed_source_discards_cached_function(self):
        result = self.relocate(stamps={'lib.pxl': 'changed'})
        self.assertIsNone(result['function'])
        self.assertEqual(result['hits'], 0)

    def test_malformed_result_metadata_is_a_cache_miss(self):
        for argument in ({}, {'result': 7}):
            with self.subTest(argument=argument):
                result = self.relocate(extra=(('RESULT_DECODE', argument),))
                self.assertIsNone(result['function'])
                self.assertEqual(result['hits'], 0)
