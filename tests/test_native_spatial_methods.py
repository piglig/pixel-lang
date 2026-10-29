"""Spatial receiver calls are typed directly and lower to spatial instructions."""
import json
import unittest
from unittest.mock import patch
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources
from tests.test_native_checked_operations import sources
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

class NativeSpatialMethodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code=compile_native_sources(sources())

    def compile(self, source):
        with patch('pixellang.text.parse_text',side_effect=AssertionError('host parser forbidden')):
            output=VM(self.code,input_text=json.dumps({'main.pxl':source}),max_steps=40_000_000,
                      max_output_chars=16_000_000,**SELFHOST_HEAP_LIMITS).run()
        return json.loads(output[0])

    def execute(self, source):
        result=self.compile(source)
        self.assertEqual(result['diagnostics'],[],result['diagnostics'])
        code=result['bytecode']
        self.assertTrue(any(i['op']=='SPACE_OP' for f in code['functions'].values() for i in f['instructions']))
        return VM(code).run()

    def test_space_values_typed_links_and_neighbors(self):
        source='''frame World = meters
record Cost {weight:int}
effect fn main(){
 let initial=Space::[World,string,Cost]("network").insert("entity-0", Point3::[World]{x:0.0,y:0.0,z:0.0},"a")
 let nodes=initial.insert("entity-1", Point3::[World]{x:0.0,y:0.0,z:2.0},"b")
 let a=nodes.require(nodes.entityId("entity-0"))
 let b=nodes.require(nodes.entityId("entity-1"))
 let linked=nodes.connect("link-0", a.id,b.id,Cost{weight:5})
 let edge=linked.requireLink(linked.linkId("link-0"))
 let moved=linked.move(b.id,Point3::[World]{x:0.0,y:0.0,z:9.0})
 let updated=moved.setLinkPayload(edge.id,Cost{weight:8})
 print(initial.size());print(nodes.size())
 print(edge.payload.weight);print(updated.requireLink(updated.linkId("link-0")).payload.weight)
 print(updated.require(edge.source).id.key);print(updated.require(edge.target).position.z)
 print(len(nodes.neighbors(a.id,3.0)));print(len(moved.neighbors(a.id,3.0)))
 print(nodes.ahead(a.id,b.id,Vec3::[World]{x:0.0,y:0.0,z:1.0},0.0))
 print(nodes.ahead(a.id,b.id,Vec3::[World]{x:0.0,y:0.0,z:1.0},3.0))
 print(len(updated.outgoing(a.id)));print(len(updated.incoming(b.id)))
 let disconnected=updated.disconnect(edge.id)
 print(disconnected.linkCount());print(disconnected.remove(b.id).size());print(updated.linkCount())
}'''
        self.assertEqual(self.execute(source),[1,2,5,8,"entity-0",9.0,1,0,True,False,1,1,0,1,1])

    def test_point_vector_and_transform_receiver_types(self):
        source='''frame World = meters
frame Local = centimeters
effect fn main(){
 let p=Point3::[World]{x:0.0,y:0.0,z:0.0}
 print(p.distance(Point3::[World]{x:0.0,y:0.0,z:3.0}))
 print(Vec3::[World]{x:1.0,y:2.0,z:3.0}.dot(Vec3::[World]{x:2.0,y:0.0,z:1.0}))
 let identity=Transform3::[World,World]{translation:Vec3::[World]{x:0.0,y:0.0,z:0.0},rotation:[[1.0,0.0,0.0],[0.0,1.0,0.0],[0.0,0.0,1.0]]}
 print(identity.then(identity).point(p).z)
 print(identity.vector(Vec3::[World]{x:1.0,y:2.0,z:3.0}).z)
}'''
        self.assertEqual(self.execute(source),[3.0,5.0,0.0,3.0])

    def test_receiver_and_arguments_evaluate_once_in_source_order(self):
        source='''frame World = meters
effect fn receiver()->Space[World,string,bool]{print("receiver");return Space::[World,string,bool]("value")}
effect fn point()->Point3[World]{print("point");return Point3::[World]{x:0.0,y:0.0,z:0.0}}
effect fn payload()->string{print("payload");return "entity"}
effect fn main(){print(receiver().insert("entity-0", point(),payload()).size())}'''
        self.assertEqual(self.execute(source),['receiver','point','payload',1])

    def test_methods_have_pure_contracts_without_hiding_operand_effects(self):
        source = """frame World = meters
fn size(scene:Space[World,string,bool])->int=scene.size()
fn distance(a:Point3[World],b:Point3[World])->float64=a.distance(b)
fn add(scene:Space[World,string,bool])->Space[World,string,bool]=scene.insert("entity-0", Point3::[World]{x:0.0,y:0.0,z:0.0},"value")
effect fn main(){
 let scene=Space::[World,string,bool]("pure")
 print(size(add(scene)));print(size(scene))
 print(distance(Point3::[World]{x:0.0,y:0.0,z:0.0},Point3::[World]{x:0.0,y:0.0,z:2.0}))
}"""
        self.assertEqual(self.execute(source),[1,0,2.0])
        for call in ('source().size()', 'scene.insert("sample",point(),"value").size()'):
            text = """frame World = meters
effect fn source()->Space[World,string,bool]=Space::[World,string,bool]("effect")
effect fn point()->Point3[World]{print("effect");return Point3::[World]{x:0.0,y:0.0,z:0.0}}
fn invalid(scene:Space[World,string,bool])->int="""+call+"\nfn main(){}"
            with self.subTest(call=call):
                diagnostics=self.compile(text)['diagnostics']
                self.assertTrue(any(item['phase']=='effect' for item in diagnostics),diagnostics)

    def test_spatial_properties_are_readonly_through_nested_paths(self):
        prefix = """frame World = meters
record Data {count:int, values:[int]}
fn change(value:inout int){value=9}
effect fn main(){
 let scene=Space::[World,Data,Data]("test").insert("entity-0", Point3::[World]{x:0.0,y:0.0,z:0.0},Data{count:1,values:[2]})
 var entity=scene.require(scene.entityId("entity-0"))
 var edge=scene.connect("link-0", entity.id,entity.id,Data{count:3,values:[4]}).requireLink(scene.linkId("link-0"))
"""
        for operation in ('entity.id=7', 'entity.position.z=7.0',
                          'entity.payload.count=7', 'entity.payload.values[0]=7',
                          'entity.payload.values.append(7)', 'change(&entity.payload.count)',
                          'edge.id=7', 'edge.payload.count=7',
                          'edge.payload.values.append(7)', 'change(&edge.payload.count)'):
            with self.subTest(operation=operation):
                diagnostics=self.compile(prefix+operation+'}')['diagnostics']
                self.assertTrue(diagnostics)
                self.assertIn('readonly',str(diagnostics).lower())
        self.assertEqual(self.execute(prefix+"""
 print(entity.payload.count);print(entity.payload.values[0]);print(edge.id.key)
 var copied=entity.payload
 copied.count=9;copied.values.append(8)
 print(copied.count);print(len(copied.values));print(entity.payload.count);print(len(entity.payload.values))
}"""),[1,2,"link-0",9,2,1,1])

    def test_invalid_receiver_frames_payloads_and_arity_are_diagnosed(self):
        prefix='frame A = meters\nframe B = meters\neffect fn main(){let scene=Space::[A,string,bool]("test");'
        for expression in ('scene.insert("sample",Point3::[B]{x:0.0,y:0.0,z:0.0},"bad")',
                           'scene.insert("sample",Point3::[A]{x:0.0,y:0.0,z:0.0},1)',
                           'scene.get()', 'scene.unknown()', 'scene.dot(scene)',
                           'Point3::[A]{x:0.0,y:0.0,z:0.0}.distance(Point3::[B]{x:0.0,y:0.0,z:0.0})'):
            with self.subTest(expression=expression):
                self.assertTrue(self.compile(prefix+'let rejected='+expression+'}')['diagnostics'])

    def test_transform_composition_rejects_wrong_frame_and_arity(self):
        prefix='frame A=meters\nframe B=centimeters\nfn invalid(first:Transform3[A,B],wrong:Transform3[A,A])'
        for body,message in (
            ('{let result=first.then(wrong)}','Type mismatch: Expected B, got A'),
            ('{let result=first.then()}','Wrong spatial operation argument count: transformThen'),
            ('{let result=first.then(1)}','Expected a coordinate transform'),
            ('{let result=first.then(wrong,wrong)}','Wrong spatial operation argument count: transformThen'),
        ):
            with self.subTest(body=body):
                diagnostics=self.compile(prefix+body+'\nfn main(){}')['diagnostics']
                self.assertTrue(diagnostics)
                self.assertTrue(any(item['phase']=='check' and item['message']==message for item in diagnostics),diagnostics)


if __name__=='__main__':unittest.main()
