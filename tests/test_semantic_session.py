"""Resident native compiler values reuse queries without filesystem caches."""
import json
from pathlib import Path
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

DRIVER = '''import "compiler_session.pxl" as session
import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "closures.pxl" as closures
import "instances.pxl" as instances
import "ir.pxl" as ir
import "assembler.pxl" as assembler
record Request { files: map[string,string], limit: int }
effect fn main() {
 match jsonDecode::[[Request]](input()) {
  .Ok(requests) => {
   var state=session.Empty()
   for request in requests {
    let result=session.Check(request.files,"main.pxl",request.limit,&state)
    let fresh=checker.Check(types.Analyze(bindings.Bind(project.Build(request.files,"main.pxl"))))
    assert(jsonStringify(jsonFrom(result.checked.diagnostics))==jsonStringify(jsonFrom(fresh.diagnostics)))
    if len(fresh.diagnostics)==0 {
     var cachedPlan=closures.Build(instances.Build(result.checked))
     var freshPlan=closures.Build(instances.Build(fresh))
     assert(jsonStringify(jsonFrom(assembler.Assemble(ir.Build(&cachedPlan))))==jsonStringify(jsonFrom(assembler.Assemble(ir.Build(&freshPlan)))))
    }
    print([result.parsed,result.parseHits,result.checkedFunctions,result.semanticHits,state.revision,len(result.checked.diagnostics),len(state.sources)])
   }
  }, .Err(error) => { print(error); assert(false) }
 }
}'''

class SemanticSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {p.name: p.read_text() for p in (Path(__file__).resolve().parents[1]/'selfhost').glob('*.pxl')}
        files['main.pxl'] = DRIVER
        cls.code = compile_native_sources(files)

    def run_edits(self, *files, limit=100000):
        return VM(self.code, input_text=json.dumps([dict(files=item, limit=limit) for item in files]),
                  max_steps=100_000_000, max_depth=1024).run()

    def test_body_edit_reuses_sibling_queries_and_unchanged_module_syntax(self):
        files={'lib.pxl':'export fn value()->int=1',
               'main.pxl':'import "lib.pxl" as lib\nfn sibling()->int=2\neffect fn main(){print(lib.value()+sibling())}'}
        edited={**files,'lib.pxl':'export fn value()->int=3'}
        results=self.run_edits(files,files,edited)
        self.assertEqual(results[0][:4],[2,0,3,0])
        self.assertEqual(results[1][:4],[0,2,0,3])
        self.assertEqual(results[2][:4],[1,1,1,2])
        self.assertEqual([r[4] for r in results],[1,2,3])

    def test_failed_contract_edit_preserves_last_successful_snapshot(self):
        files={'lib.pxl':'export fn value()->int=1',
               'main.pxl':'import "lib.pxl" as lib\nfn use()->int=lib.value()\nfn main(){}'}
        bad={**files,'lib.pxl':'export fn value()->string="bad"'}
        results=self.run_edits(files,bad,files)
        self.assertGreater(results[1][5],0)
        self.assertEqual(results[1][4],1)
        self.assertEqual(results[2][:4],[0,2,0,3])
        self.assertEqual(results[2][4],2)

    def test_capacity_pressure_discards_cache_without_changing_results(self):
        files={'main.pxl':'fn value()->int=1\nfn main(){}'}
        results=self.run_edits(files,files,limit=1)
        self.assertEqual([r[:4] for r in results],[[1,0,2,0],[1,0,2,0]])
        self.assertEqual([r[-1] for r in results],[0,0])

    def test_removed_import_and_nominal_relocation_match_fresh_compilation(self):
        files={'lib.pxl':'export record Box{value:int}',
               'main.pxl':'import "lib.pxl" as lib\nfn value()->lib.Box=lib.Box{value:1}\neffect fn main(){print(value().value)}'}
        moved={**files,'lib.pxl':'record Added{name:string}\n'+files['lib.pxl']}
        removed={'main.pxl':'effect fn main(){print(1)}'}
        results=self.run_edits(files,moved,removed)
        self.assertEqual(results[1][3],2)
        self.assertEqual(results[-1][-1],1)
