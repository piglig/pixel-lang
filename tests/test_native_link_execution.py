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
 let a = empty.insert("entity-0", Point3::[World]{x:0.0,y:0.0,z:0.0}, "a")
 let b = a.insert("entity-1", Point3::[World]{x:0.0,y:0.0,z:2.0}, "b")
 var weight = Weight{values:[5]}
 let linked = b.connect("link-0", b.require(b.entityId("entity-0")).id, b.require(b.entityId("entity-1")).id, weight)
 weight.values.append(99)
 let edge: Link[World, string, Weight] = linked.requireLink(linked.linkId("link-0"))
 let moved = linked.move(linked.require(linked.entityId("entity-1")).id,Point3::[World]{x:0.0,y:0.0,z:9.0})
 let changed = moved.setLinkPayload(edge.id,Weight{values:[8]})
 print(edge.payload.values)
 print(changed.requireLink(changed.linkId("link-0")).payload.values)
 print(linked.require(edge.target).position.z)
 print(changed.require(edge.target).position.z)
 print(changed.require(edge.source).id.key)
 print(len(changed.outgoing(changed.require(changed.entityId("entity-0")).id)))
 print(len(changed.incoming(changed.require(changed.entityId("entity-0")).id)))
 let detached = changed.disconnect(edge.id)
 print(detached.linkCount())
 print(detached.remove(detached.require(detached.entityId("entity-1")).id).size())
}
''')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [[5], [8], 2.0, 9.0, "entity-0", 1, 0, 0, 1])

    def test_link_payload_type_is_checked_by_native_frontend(self):
        result = self.compile('''frame World = meters
effect fn main() {
 let a = Space::[World,string,int]("network").insert("entity-0", Point3::[World]{x:0.0,y:0.0,z:0.0},"a")
 let wrong = a.connect("link-0", a.require(a.entityId("entity-0")).id,a.require(a.entityId("entity-0")).id,"wrong weight")
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
