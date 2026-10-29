"""Receiver calls and typed readonly properties survive native PNG delivery."""
from tests.native_compiler import native_service
import unittest
from unittest.mock import patch

from pixellang.vm import VM


class NativeSpatialMethodDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def result(self, operation, **args):
        with patch('pixellang.text.parse_text', side_effect=AssertionError('host frontend forbidden')):
            response = self.compiler.request(dict(version=1, operation=operation, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def test_receiver_properties_execute_recover_and_recompile(self):
        source = '''frame World = meters
record Cost {weight:int}
effect fn main(){
 let origin=Point3::[World]{x:0.0,y:0.0,z:0.0}
 let destination=Point3::[World]{x:0.0,y:0.0,z:1.0}
 let scene=Space::[World,string,Cost]("delivery").insert("entity-0", origin,"a").insert("entity-1", destination,"b")
 let a=scene.require(scene.entityId("entity-0"))
 let b=scene.require(scene.entityId("entity-1"))
 let linked=scene.connect("link-0", a.id,b.id,Cost{weight:7})
 let edge=linked.requireLink(linked.linkId("link-0"))
 print(a.position.distance(b.position))
 print(linked.require(edge.target).payload)
 print(edge.payload.weight)
 print(edge.id.key)
 print(len(scene.neighbors(a.id,2.0)))
}'''
        files = {'main.pxl': source}
        expected = [1.0, 'b', 7, 'link-0', 1]
        original = self.result('compile', files=files)['bytecode']
        self.assertEqual(VM(original).run(), expected)
        image = self.result('export-png', files=files)['image']
        self.assertEqual(VM(self.result('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.result('recover-png', image=image)
        self.assertEqual(VM(self.result('compile', **recovered)['bytecode']).run(), expected)
        text = recovered['files'][recovered['entry']]
        self.assertIn('.insert(', text)
        self.assertIn('.payload', text)
        self.assertNotIn('spaceInsert(', text)
        changed = source.replace('z:1.0', 'z:9.0')
        self.assertEqual(VM(self.result('compile', files={'main.pxl': changed})['bytecode']).run(),
                         [9.0, 'b', 7, 'link-0', 0])

    def test_typed_identities_forks_maps_and_snapshot_api_survive_delivery(self):
        source = '''frame World = meters
fn Label(id: EntityId[World,string]) -> string = id.key
effect fn main() {
 let empty = Space::[World,string,int]("orders")
 let a: EntityId[World,string] = empty.entityId("订单-42")
 let b = empty.entityId("other")
 let road: LinkId[World,string,int] = empty.linkId("route")
 let p = Point3::[World]{x:0.0,y:0.0,z:0.0}
 let first = empty.insert(a.key,p,"pending").insert(b.key,p,"other")
 let captured = first.require(a)
 let destination = Point3::[World]{x:0.0,y:0.0,z:3.0}
 let second = first.move(a,destination).setPayload(a,"paid")
 let branch = empty.insert(a.key,p,"cancelled")
 print(Label(a))
 print(captured.id == branch.require(a).id)
 print(captured.position.z)
 print(captured.payload)
 print(second.require(a).position.z)
 print(second.require(a).payload)
 var labels: map[EntityId[World,string],string] = map[EntityId[World,string],string]{}
 labels[a] = "stored"
 print(labels[branch.require(a).id])
 let linked = second.connect(road.key,a,b,7)
 let edge = linked.requireLink(road)
 print(edge.source == a)
 print(edge.target == b)
 var weights: map[LinkId[World,string,int],int] = map[LinkId[World,string,int],int]{}
 weights[edge.id] = 9
 print(weights[road])
 let removed = linked.removeCascade(a)
 match removed.get(a) { .None => print("absent"), .Some(value) => print(value.payload) }
 match removed.getLink(road) { .None => print("disconnected"), .Some(value) => print(value.payload) }
 print(linked.requireLink(road).payload)
 print(removed.size())
 print(removed.insert(a.key,p,"reopened").require(a).id == a)
}'''
        expected = ['订单-42', True, 0.0, 'pending', 3.0, 'paid', 'stored', True, True,
                    9, 'absent', 'disconnected', 7, 1, True]
        files = {'main.pxl': source}
        code = self.result('compile', files=files)['bytecode']
        vm = VM(code)
        while len(vm.output) < 9: vm.step(snapshot=False)
        checkpoint = vm.checkpoint()
        self.assertEqual(vm.run(), expected)
        final = vm.checkpoint()
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(), expected)
        self.assertEqual(vm.checkpoint(), final)
        image = self.result('export-png', files=files)['image']
        self.assertEqual(VM(self.result('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.result('recover-png', image=image)
        self.assertEqual(VM(self.result('compile', **recovered)['bytecode']).run(), expected)

    def test_snapshot_values_cannot_be_used_as_identities(self):
        prefix = '''frame World = meters
effect fn main() {
 let space = Space::[World,string,int]("room")
 let p = Point3::[World]{x:0.0,y:0.0,z:0.0}
 let populated = space.insert("a",p,"value")
 let entity = populated.require(populated.entityId("a"))
'''
        for expression in ('populated.move(entity,p)', 'populated.get("a")',
                           'populated.get(0)', 'populated.remove(entity)',
                           'populated.get(populated.linkId("a"))',
                           'populated.connect("edge",entity,entity,1)',
                           'EntityId::[World,string]{key:"a"}',
                           'LinkId::[World,string,int]{key:"edge"}'):
            with self.subTest(expression=expression):
                response = self.compiler.request(dict(version=1,operation='compile',
                    files={'main.pxl':prefix+'let invalid = '+expression+'\n}'}))
                self.assertTrue(response.get('diagnostics'), response)


if __name__ == '__main__':
    unittest.main()
