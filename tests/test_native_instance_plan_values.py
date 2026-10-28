"""Generic instance planning uses value-owned indexes and published results."""
import json
from pathlib import Path
import re
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "model.pxl" as model
import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "instances.pxl" as instances
record Request { source:string, text:string, operation:string, files:map[string,string] }
record Output { instances:[model.FunctionInstance], edges:[model.InstanceEdge], types:[model.TypeInfo], symbols:[model.Symbol], entry:int, diagnostics:[model.Diagnostic] }
effect fn main() {
 match jsonDecode::[Request](input()) {
  .Ok(request) => { Dispatch(request) },
  .Err(error) => { print(error.message) }
 }
}
effect fn Dispatch(request:Request) {
 let checked = checker.Check(types.Analyze(bindings.Bind(project.Build(request.files,request.source))))
 let planned = instances.Build(checked)
 let published = planned.instances
 if len(published)>0 {
  let count = len(published[0].arguments)
  var edited = published
  edited[0].arguments.append(-1)
  assert(len(published[0].arguments)==count)
 }
 let edges = planned.edges
 if len(edges)>0 {
  let target = edges[0].target
  var edited = edges
  edited[0].target = -1
  assert(edges[0].target==target)
 }
 print(Output{instances:published,edges,types:planned.checked.resolved.types,symbols:planned.checked.resolved.bound.symbols,entry:planned.entry,diagnostics:planned.diagnostics})
}
'''


def sources():
    files = {'main.pxl': DRIVER}
    pending = re.findall(r'import "([^"]+)"', DRIVER)
    while pending:
        name = pending.pop()
        if name in files:
            continue
        files[name] = (ROOT / 'selfhost' / name).read_text()
        pending.extend(re.findall(r'import "([^"]+)"', files[name]))
    return files


class NativeInstancePlanValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = compile_native_sources(sources())

    def test_stateful_child_targets_and_transitive_helpers_remain_reachable(self):
        source = '''fn Identity[T](value:T) -> T = value
fn Add(previous:int, delta:int) -> int = Identity(previous+delta)
flow Counter() step(delta:int) -> int {
 state value:int=0
 let add=Add(value,delta)
 next value=add
 output add
}
flow Parent() step(delta:int) -> int {
 instance child=Counter()
 output child.step(delta)
}
effect fn main() { let instance=Parent(); print(instance.step(1)) }
'''
        result = VM(self.code, input_text=json.dumps(dict(source='main.pxl', text='',
                    operation='instances', files={'main.pxl': source})),
                    max_steps=30_000_000, max_heap_items=32_000_000).run()[0]
        if isinstance(result, str):
            result = json.loads(result)
        self.assertEqual(result['diagnostics'], [])
        names = [result['symbols'][i['symbol']]['name'] for i in result['instances']]
        self.assertEqual(set(names), {'main', 'Parent', 'Counter', 'Add', 'Identity'})
        graph_edges = [e for e in result['edges'] if e['kind'] == 'flow-node']
        self.assertEqual({(names[e['caller']], names[e['target']]) for e in graph_edges},
                         {('Parent', 'Counter')})
        call_edges = {(names[e['caller']], names[e['target']])
                      for e in result['edges'] if e['kind'] == 'call'}
        self.assertIn(('Counter', 'Add'), call_edges)
        self.assertIn(('Add', 'Identity'), call_edges)
