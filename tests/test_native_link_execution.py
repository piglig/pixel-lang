import json
from pathlib import Path
import unittest
from tests.native_compiler import compile_native_sources
from tests.test_native_checked_operations import sources
from pixellang.vm import VM


class NativeLinkExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = compile_native_sources(sources())

    def compile(self, source):
        output = VM(self.compiler, input_text=json.dumps({'main.pxl': source}),
                    max_steps=50_000_000, max_heap_items=16_000_000).run()
        return json.loads(output[0])

    def test_native_link_payloads_motion_and_history(self):
        result = self.compile('''frame World = meters
record Weight { values: [int] }
effect fn main() {
 let empty = Space::[World, string, Weight]("network")
 let a = empty.insert(Point3::[World]{x:0.0,y:0.0,z:0.0}, "a")
 let b = a.insert(Point3::[World]{x:0.0,y:0.0,z:2.0}, "b")
 var weight = Weight{values:[5]}
 let linked = b.connect(b.get(0), b.get(1), weight)
 weight.values.append(99)
 let edge: Link[World, string, Weight] = linked.getLink(0)
 let moved = linked.move(linked.get(1),Point3::[World]{x:0.0,y:0.0,z:9.0})
 let changed = moved.setLinkPayload(edge,Weight{values:[8]})
 print(edge.payload.values)
 print(changed.getLink(0).payload.values)
 print(linked.target(edge).position.z)
 print(changed.target(edge).position.z)
 print(changed.source(edge).id)
 print(len(changed.outgoing(changed.get(0))))
 print(len(changed.incoming(changed.get(0))))
 let detached = changed.disconnect(edge)
 print(detached.linkCount())
 print(detached.remove(detached.get(1)).size())
}
''')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [[5], [8], 2.0, 9.0, 0, 1, 0, 0, 1])

    def test_link_payload_type_is_checked_by_native_frontend(self):
        result = self.compile('''frame World = meters
effect fn main() {
 let a = Space::[World,string,int]("network").insert(Point3::[World]{x:0.0,y:0.0,z:0.0},"a")
 let wrong = a.connect(a.get(0),a.get(0),"wrong weight")
}
''')
        self.assertTrue(result['diagnostics'])
        self.assertTrue(any('type' in d['message'].lower() for d in result['diagnostics']))

    def test_weighted_routes_propagate_across_flow_steps(self):
        source = (Path(__file__).resolve().parents[1] /
                  'examples/weighted-routes/main.pxl').read_text()
        result = self.compile(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [
            [0, 5, 20, -1], [0, 5, 7, 21], [0, 5, 7, 8],
            [0, 5, 20, 1], False,
        ])
