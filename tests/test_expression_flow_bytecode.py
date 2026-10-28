"""Expression-flow bytecode boundaries and atomic participant execution."""
from copy import deepcopy
import unittest
from pixellang.vm import VM,validate_bytecode
from pixellang.model import PixelError
from pixellang.typesys import ERROR_FIELDS


def op(code,arg=None,node=0):return dict(op=code,arg=arg,node=node,module=0)


def program():
    child=dict(module=0,node=10,name='Child',stateful=True,
        inputs=[dict(name='delta',node=1,slot=0,type='int')],initialInputs=[],
        states=[dict(node=2,name='n',slot=1,type='int',initializer='initial')],
        updates=[dict(node=3,state=2,expression=4)],children=[],values=[],order=[],
        dependencies=[dict(source=1,target=3,reference=7),dict(source=2,target=3,reference=8),dict(source=2,target=6,reference=5)],
        returnNode=6,output=dict(node=5,slot=2,type='int'))
    parent=dict(module=0,node=30,name='Parent',stateful=True,
        inputs=[dict(name='enabled',node=1,slot=0,type='bool'),dict(name='delta',node=2,slot=1,type='int')],
        initialInputs=[],states=[],updates=[],children=[dict(node=20,name='child',function='child',initializers=[])],
        values=[],order=[],dependencies=[dict(source=20,target=18,reference=14)],
        returnNode=18,output=dict(node=17,slot=2,type='int'))
    main=[op('FLOW_NEW','parent'),op('STORE',0)]
    for enabled in (False,True,False,True):
        main.extend([op('LOAD',0),op('PUSH',3 if enabled else 2),op('PUSH',0),
                     op('FLOW_STEP','Flow<fn(bool,int)->(int)>'),op('PRINT')])
    main.extend([op('PUSH',1),op('RETURN')])
    return dict(magic='PIXELVM',vm_version='0.9',language_version='0.9',compiler_version='test',
        records={'Error':dict(ERROR_FIELDS)},enums={},effectFunctions=['main'],flows={'parent':parent,'child':child},
        constants=[dict(type='int',value=5),dict(type='int',value=0),dict(type='bool',value=False),dict(type='bool',value=True),dict(type='int',value=-1)],
        entry=['main'],functions={
            'main':dict(params=[],locals=1,result='unit',instructions=main),
            'initial':dict(params=[],locals=0,result='int',instructions=[op('PUSH',1),op('RETURN')]),
            'child':dict(params=['int'],locals=3,result='int',instructions=[op('FLOW_READ',2,2),op('STORE',1,2),
                op('LOAD',1,8),op('LOAD',0,7),op('ADD',node=4),op('FLOW_STAGE',2,3),op('LOAD',1,5),
                op('STORE',2,5),op('LOAD',2,5),op('RETURN',node=6)]),
            'parent':dict(params=['bool','int'],locals=3,result='int',instructions=[op('LOAD',0,12),
                op('JUMP_IF_FALSE',5,12),op('LOAD',1,13),op('FLOW_CHILD_STEP',dict(node=20,reference=14),15),
                op('JUMP',6,15),op('PUSH',4,16),op('STORE',2,17),op('LOAD',2,17),op('RETURN',node=18)])})


class ExpressionFlowBytecodeTests(unittest.TestCase):
    def test_conditional_child_keeps_state_across_skipped_activations(self):
        vm=VM(program())
        self.assertEqual(vm.run(),[-1,0,-1,5])
        self.assertEqual(vm.flow_runtime.instances[0].cells[((20,),2)],10)
        self.assertEqual(vm.flow_runtime.instances[0].steps,4)

    def test_active_state_without_next_is_retained_and_checkpointed(self):
        code=program()
        code['flows']['child']['updates']=[]
        code['flows']['child']['dependencies']=[dict(source=2,target=6,reference=5)]
        del code['functions']['child']['instructions'][2:6]
        vm=VM(code)
        while not (vm.flow_runtime.transaction and (20,) in vm.flow_runtime.transaction.entered):
            vm.step(snapshot=False)
        checkpoint=vm.checkpoint()
        self.assertEqual(vm.run(),[-1,0,-1,0])
        final=vm.checkpoint()
        self.assertEqual(vm.flow_runtime.instances[0].cells[((20,),2)],0)
        self.assertEqual(vm.flow_runtime.instances[0].steps,4)
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(),[-1,0,-1,0])
        self.assertEqual(vm.checkpoint(),final)

    def test_declared_update_cannot_be_skipped_at_runtime(self):
        code=program()
        instructions=code['functions']['child']['instructions']
        # Keep the validated update instruction but jump over its computation.
        instructions.insert(2,op('JUMP',7))
        vm=VM(code)
        with self.assertRaisesRegex(PixelError,'every declared state update'):
            vm.run()
        self.assertEqual(vm.flow_runtime.instances[0].steps,1)
        self.assertEqual(vm.flow_runtime.instances[0].cells[((20,),2)],0)

    def test_child_path_and_staged_updates_survive_vm_checkpoint(self):
        vm=VM(program())
        while not (vm.flow_runtime.transaction and vm.flow_runtime.transaction.staged):vm.step(snapshot=False)
        self.assertEqual(vm.call_stack[-1].flow_path,(20,))
        before=vm.checkpoint()
        self.assertEqual(vm.run(),[-1,0,-1,5])
        after=vm.checkpoint()
        vm.restore_checkpoint(before)
        self.assertEqual(vm.run(),[-1,0,-1,5])
        self.assertEqual(vm.checkpoint(),after)

    def test_parent_failure_does_not_commit_completed_child(self):
        code=program()
        code['functions']['parent']['instructions'][-3:-3]=[op('PUSH',1),op('DIV')]
        # Enter an enabled branch on the first activation.
        code['functions']['main']['instructions'][3]['arg']=3
        vm=VM(code)
        with self.assertRaises(PixelError):vm.run()
        self.assertIsNone(vm.flow_runtime.transaction)
        self.assertEqual(vm.flow_runtime.instances[0].cells[((20,),2)],0)
        self.assertEqual(vm.flow_runtime.instances[0].steps,0)

    def test_invalid_child_identity_and_declaration_provenance_are_rejected(self):
        for mutation in ('child','outside','ordinary','output','order','stage'):
            code=program()
            if mutation=='child':code['functions']['parent']['instructions'][3]['arg']['node']=999
            elif mutation=='outside':code['functions']['main']['instructions'][0]=op('FLOW_CHILD_STEP',dict(node=20,reference=14))
            elif mutation=='ordinary':code['functions']['parent']['instructions'][3]=op('CALL','child',15)
            elif mutation=='output':code['flows']['parent']['output']['slot']=0
            elif mutation=='order':code['flows']['parent']['order']=[20]
            else:code['functions']['child']['instructions'][5]['arg']=99
            with self.subTest(mutation=mutation),self.assertRaises(PixelError):validate_bytecode(code)

    def test_durable_cell_types_follow_child_declaration_paths(self):
        from types import SimpleNamespace
        from pixellang.native_state import cell_types
        vm=VM(program());vm.run()
        vm.event_binding=SimpleNamespace(handle=vm.flow_runtime.instances[0].handle)
        self.assertEqual(cell_types(vm),{((20,),2):'int'})
        from pixellang.flow_debug import instance_values
        self.assertEqual(instance_values(vm,vm.event_binding.handle)['state'],{'child/n':10})

    def test_repeated_explicit_child_step_is_rejected_even_without_state(self):
        for stateful in (True,False):
            code=program()
            code['functions']['parent']['instructions']=[op('LOAD',1,13),
                op('FLOW_CHILD_STEP',dict(node=20,reference=14),15),op('POP'),op('LOAD',1,13),
                op('FLOW_CHILD_STEP',dict(node=20,reference=14),15),op('STORE',2,17),op('LOAD',2,17),op('RETURN',node=18)]
            if not stateful:
                child=code['flows']['child']
                child.update(stateful=False,states=[],updates=[],dependencies=[dict(source=1,target=6,reference=5)])
                code['functions']['child']['instructions']=[op('LOAD',0,5),op('STORE',2,5),op('LOAD',2,5),op('RETURN',node=6)]
            vm=VM(code)
            if not stateful:
                invalid = deepcopy(code)
                invalid['flows']['parent']['stateful'] = False
                with self.assertRaisesRegex(PixelError, 'stateful'):
                    validate_bytecode(invalid)
            with self.subTest(stateful=stateful),self.assertRaisesRegex(PixelError,'already advanced'):vm.run()
            self.assertEqual(vm.flow_runtime.instances[0].steps,0)
            self.assertIsNone(vm.flow_runtime.transaction)
            if stateful:self.assertEqual(vm.flow_runtime.instances[0].cells[((20,),2)],0)


if __name__=='__main__':unittest.main()
