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
 let scene=Space::[World,string,Cost]("delivery").insert(origin,"a").insert(destination,"b")
 let a=scene.get(0)
 let b=scene.get(1)
 let linked=scene.connect(a,b,Cost{weight:7})
 let edge=linked.getLink(0)
 print(a.position.distance(b.position))
 print(linked.target(edge).payload)
 print(edge.payload.weight)
 print(edge.id)
 print(len(scene.neighbors(a,2.0)))
}'''
        files = {'main.pxl': source}
        expected = [1.0, 'b', 7, 0, 1]
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
                         [9.0, 'b', 7, 0, 0])


if __name__ == '__main__':
    unittest.main()
