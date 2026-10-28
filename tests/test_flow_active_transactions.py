"""Transaction participant semantics, independent of expression IR emission."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from pixellang.model import PixelError
from pixellang.native_flow import FlowRuntime, FlowHandle, FlowInstance, Transaction


class TransactionVM:
    def __init__(self):
        self.bytecode={'flows':{
            'parent':{'states':[{'node':1,'type':'int'}],'updates':[{'state':1}]},
            'child':{'states':[{'node':2,'type':'int'}],'updates':[{'state':2}]}}}
        self.space_identities={'original'}
        self.spatial_items=2

    def fail_data(self,message,span=None,**details):
        raise PixelError('runtime',message)

    def typed(self,value,typ,span):
        if typ!='int' or type(value) is not int:self.fail_data('Invalid state type')

    def freeze_spatial_payload(self,value,span):return value
    def thaw_spatial_payload(self,value,span):return value


def frame(name,path,role=''):
    return SimpleNamespace(function=name,flow_path=path,flow_root=0,flow_role=role)


class FlowActiveTransactionTests(unittest.TestCase):
    def setUp(self):
        self.vm=TransactionVM()
        self.root=frame('parent',(),'step')
        self.child=frame('child',(10,))
        self.instance=FlowInstance(FlowHandle(0,'parent','fn()->(int)'),
                                   {((),1):3,((10,),2):7,((20,),2):11})
        self.runtime=FlowRuntime(instances={0:self.instance})
        self.runtime.transaction=Transaction('step',self.instance,set(self.vm.space_identities),2)
        self.runtime.enter(self.vm,self.root)

    def test_skipped_children_retain_state_when_parent_commits(self):
        self.runtime.stage(self.vm,self.root,1,4,None)
        self.runtime.returned(self.vm,self.root,3,None)
        self.assertEqual(self.instance.cells,{((),1):4,((10,),2):7,((20,),2):11})
        self.assertEqual(self.instance.steps,1)
        self.assertIsNone(self.runtime.transaction)

    def test_active_child_with_no_updates_keeps_state(self):
        self.vm.bytecode['flows']['child']['updates']=[]
        self.runtime.enter(self.vm,self.child)
        self.runtime.returned(self.vm,self.child,7,None)
        self.runtime.stage(self.vm,self.root,1,4,None)
        self.runtime.returned(self.vm,self.root,3,None)
        self.assertEqual(self.instance.cells,{((),1):4,((10,),2):7,((20,),2):11})
        self.assertEqual(self.instance.steps,1)

    def test_parent_retains_state_while_child_updates_atomically(self):
        self.vm.bytecode['flows']['parent']['updates']=[]
        self.runtime.enter(self.vm,self.child)
        self.runtime.stage(self.vm,self.child,2,8,None)
        self.runtime.returned(self.vm,self.child,7,None)
        self.assertEqual(self.instance.cells[((10,),2)],7)
        self.runtime.returned(self.vm,self.root,3,None)
        self.assertEqual(self.instance.cells,{((),1):3,((10,),2):8,((20,),2):11})

    def test_child_return_does_not_commit_before_parent(self):
        self.runtime.enter(self.vm,self.child)
        self.runtime.stage(self.vm,self.child,2,8,None)
        self.runtime.returned(self.vm,self.child,7,None)
        self.assertEqual(self.instance.cells[((10,),2)],7)
        self.assertEqual(self.runtime.read(self.vm,self.child,2,None),7)
        self.runtime.stage(self.vm,self.root,1,4,None)
        self.runtime.returned(self.vm,self.root,3,None)
        self.assertEqual(self.instance.cells,{((),1):4,((10,),2):8,((20,),2):11})

    def test_repeated_child_activation_is_rejected_before_second_step(self):
        self.runtime.enter(self.vm,self.child)
        self.runtime.stage(self.vm,self.child,2,8,None)
        self.runtime.returned(self.vm,self.child,7,None)
        with self.assertRaisesRegex(PixelError,'already advanced'):
            self.runtime.enter(self.vm,self.child)
        self.runtime.abort(self.vm)
        self.assertEqual(self.instance.cells[((10,),2)],7)
        self.assertEqual(self.instance.steps,0)

    def test_missing_child_update_prevents_publication(self):
        self.runtime.enter(self.vm,self.child)
        with self.assertRaisesRegex(PixelError,'every declared state update'):
            self.runtime.returned(self.vm,self.child,7,None)
        self.runtime.abort(self.vm)
        self.assertEqual(self.instance.cells[((10,),2)],7)
        self.assertEqual(self.instance.steps,0)

    def test_parent_failure_discards_completed_child_updates(self):
        self.runtime.enter(self.vm,self.child)
        self.runtime.stage(self.vm,self.child,2,8,None)
        self.runtime.returned(self.vm,self.child,7,None)
        with self.assertRaisesRegex(PixelError,'every declared state update'):
            self.runtime.returned(self.vm,self.root,3,None)
        self.runtime.abort(self.vm)
        self.assertEqual(self.instance.cells,{((),1):3,((10,),2):7,((20,),2):11})
        self.assertEqual(self.instance.steps,0)

    def test_activation_participants_survive_transaction_snapshot(self):
        self.runtime.enter(self.vm,self.child)
        self.runtime.stage(self.vm,self.child,2,8,None)
        restored=deepcopy(self.runtime)
        self.assertEqual(restored.transaction.entered,{():'parent',(10,):'child'})
        restored.returned(self.vm,self.child,7,None)
        restored.stage(self.vm,self.root,1,4,None)
        restored.returned(self.vm,self.root,3,None)
        self.assertEqual(restored.instances[0].cells[((10,),2)],8)
        self.assertEqual(self.instance.cells[((10,),2)],7)


class FlowParticipantVMTests(unittest.TestCase):
    def test_nested_activation_checkpoint_replays_one_atomic_commit(self):
        from pixellang.vm import VM
        from tests.test_expression_flow_bytecode import program
        code=program()
        code['functions']['main']['instructions'][3]['arg']=3
        vm=VM(code)
        while not (vm.flow_runtime.transaction and vm.flow_runtime.transaction.staged):
            vm.step(snapshot=False)
        self.assertEqual(vm.flow_runtime.transaction.entered,{():'parent',(20,):'child'})
        self.assertEqual(vm.flow_runtime.instances[0].cells[((20,),2)],0)
        checkpoint=vm.checkpoint()
        self.assertEqual(vm.run(),[0,5,-1,10])
        final=vm.checkpoint()
        self.assertEqual(vm.flow_runtime.instances[0].cells[((20,),2)],15)
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(),[0,5,-1,10])
        self.assertEqual(vm.checkpoint(),final)

    def test_cancellation_after_child_return_does_not_publish_child_state(self):
        from pixellang.vm import VM
        from tests.test_expression_flow_bytecode import program
        code=program()
        code['functions']['main']['instructions'][3]['arg']=3
        vm=VM(code)
        while not (vm.flow_runtime.transaction and vm.flow_runtime.transaction.staged
                   and vm.call_stack[-1].function=='parent'):
            vm.step(snapshot=False)
        vm.cancel()
        with self.assertRaises(PixelError):vm.step(snapshot=False)
        self.assertEqual(vm.flow_runtime.instances[0].cells[((20,),2)],0)
        self.assertEqual(vm.flow_runtime.instances[0].steps,0)
        self.assertIsNone(vm.flow_runtime.transaction)


if __name__=='__main__':unittest.main()
