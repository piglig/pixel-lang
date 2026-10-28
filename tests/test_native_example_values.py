"""Published algorithm, flow and spatial examples use the unified value model."""
from tests.native_compiler import native_service
import unittest
from pathlib import Path

from pixellang.vm import VM

ROOT=Path(__file__).resolve().parents[1]


class NativeExampleValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler=native_service()
        cls.addClassCleanup(cls.compiler.close)

    def run_example(self,name,entry='main.pxl'):
        directory=ROOT/'examples'/name
        files={str(p.relative_to(directory)):p.read_text() for p in directory.rglob('*.pxl')}
        files.update({'std/'+p.name:p.read_text() for p in (ROOT/'pixellang/stdlib').glob('*.pxl')})
        result=self.compiler.request(dict(version=1,operation='compile',files=files,entry=entry))
        self.assertEqual(result['status'],'ok',result)
        return VM(result['result']['bytecode'],max_steps=1_000_000).run()

    def test_value_algorithms(self):
        self.assertEqual(self.run_example('matrix'),[[[19,22],[43,50]]])
        self.assertEqual(self.run_example('word-count'),['space: 2','is: 2','code: 2','&&: 1'])
        self.assertEqual(self.run_example('data-report','tests/sort_test.pxl'),[])

    def test_independent_flow_instances_and_pure_pipeline_expressions(self):
        self.assertEqual(self.run_example('native-counter'),[1,3,10,6])
        self.assertEqual(self.run_example('native-pipeline'),[24,16])
        self.assertEqual(self.run_example('native-orders','instances.pxl'),['A',1299,True,'B',2499,True,True])

    def test_spatial_values_and_typed_route_propagation(self):
        self.assertEqual(self.run_example('native-space'),[1,0,2.0,3.0,True])
        self.assertEqual(self.run_example('weighted-routes'),
                         [[0,5,20,-1],[0,5,7,21],[0,5,7,8],[0,5,20,1],False])

    def test_elevator_state_scene_and_rejected_transition(self):
        self.assertEqual(self.run_example('native-elevator'),[
            [0,3,0],False,[0],'rejected: Cannot open doors during a trip',
            [1,3,0],False,[1],[2,3,0],False,[2],[3,3,1],True,[3],
            [3,1,0],False,[3],[2,1,0],False,[2],[1,1,1],True,[1],
            [0,0,1],True,[0],9.0,1])

    def test_elevator_rejection_before_initial_scene_and_after_motion(self):
        directory=ROOT/'examples/native-elevator'
        files={p.name:p.read_text() for p in directory.glob('*.pxl')}
        files['verify.pxl']='''import "main.pxl" as elevator
import "model.pxl" as model
import "controller.pxl" as controller
effect fn main(){
 let lift=elevator.Elevator()
 match lift.step(.Request(-1)) {
  .Ok(view)=>{print("unexpected")},
  .Err(reason)=>{match reason {.InvalidFloor(floor)=>{print(floor)},.DoorsOpen=>{print("wrong")},.InTransit=>{print("wrong")}}}}
 match lift.step(.Request(3)) {.Ok(view)=>{print(view.status.floor);print(view.status.target)},.Err(reason)=>{print("unexpected")}}
 match lift.step(.Open) {.Ok(view)=>{print("unexpected")},.Err(reason)=>{print(controller.Message(reason))}}
 match lift.step(.Tick) {.Ok(view)=>{print(view.status.floor);print(view.status.target)},.Err(reason)=>{print("unexpected")}}
 match controller.Control(model.Status{floor:0,target:3,doorsOpen:true},.Tick) {
  .Ok(status)=>{print("unexpected")},.Err(reason)=>{print(controller.Message(reason))}}
}'''
        response=self.compiler.request(dict(version=1,operation='compile',files=files,entry='verify.pxl'))
        self.assertEqual(response['status'],'ok',response)
        vm=VM(response['result']['bytecode'])
        self.assertEqual(vm.run(),[-1,0,3,'Cannot open doors during a trip',1,3,'Close the doors before moving'])
        self.assertEqual(vm.flow_runtime.instances[0].steps,4)
