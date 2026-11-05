"""Inspection limits and alias handling must not alter application state."""
import unittest
from types import SimpleNamespace
from pixellang.application_changes import spaces_in_state, describe_changes, value_preview
from pixellang.spatial_values import SpaceValue, CoordinateFrame, SpatialValue, FrozenValue
from pixellang.vm import VM
from tests.test_native_spatial_vm import program


class ApplicationChangesTests(unittest.TestCase):
    def setUp(self):
        self.frame = CoordinateFrame('@0:0','meters')
        self.point = SpatialValue('Point3',self.frame,(0.,0.,0.))
        self.empty = SpaceValue('world', self.frame,'int','int')
        self.vm = VM(program())
        self.runtime = SimpleNamespace(vm=self.vm)

    def state(self, values):
        return SimpleNamespace(flow_runtime=SimpleNamespace(instances={0:SimpleNamespace(
            cells={((),i):value for i,value in enumerate(values)})}))

    def test_shared_containers_are_inspected_at_each_distinct_state_path(self):
        scene = self.empty.insert('a',self.point,1)
        aliased = FrozenValue('Space<@0:0,int,int>[]',(scene,))
        before = spaces_in_state(self.state([aliased,aliased]))
        edited = FrozenValue('Space<@0:0,int,int>[]',(scene.set_payload(scene.entity_id('a'),2),))
        after = spaces_in_state(self.state([edited,aliased]))
        self.assertEqual(len(before[0]),2)
        self.assertEqual(describe_changes(before,after,self.runtime)['total'],1)

    def test_rows_and_values_are_bounded_and_do_not_spend_execution_budgets(self):
        scene = self.empty
        for i in range(120):scene=scene.insert(str(i),self.point,i)
        report=describe_changes(({'state':self.empty},False),({'state':scene},False),self.runtime)
        self.assertEqual(report['total'],120)
        self.assertEqual(len(report['spaces'][0]['rows']),100)
        self.assertTrue(report['spaces'][0]['truncated'])
        before=self.vm.checkpoint()
        text=value_preview(FrozenValue('int[]',tuple(range(2000))),self.runtime)
        self.assertIn('preview budget',text)
        self.assertEqual(self.vm.checkpoint(),before)

    def test_space_discovery_limit_is_explicit(self):
        result,truncated=spaces_in_state(self.state([self.empty]*65))
        self.assertEqual(len(result),64)
        self.assertTrue(truncated)


if __name__=='__main__':unittest.main()
