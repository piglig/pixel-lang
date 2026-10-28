"""Scoped inout access preserves value isolation across calls, failures and replay."""
import unittest
from pixellang.model import PixelError
from pixellang.vm import VM
from tests.bytecode_helpers import program, op


class ValueAccessTests(unittest.TestCase):
    def vm(self, body=None, **options):
        code=program([op('LOAD',0),op('RETURN')])

        code['constants']=[dict(type='int',value=1),dict(type='int',value=9),dict(type='str',value='failure')]
        code['functions']['main'].update(locals=2,instructions=body or [op('PUSH',0),op('RETURN')])
        code['functions']['edit']=dict(params=['inout<int>'],locals=1,result='int',instructions=[
            op('LOAD',0),op('PUSH',0),op('ADD'),op('STORE',0),op('LOAD',0),op('RETURN')])
        return VM(code,**options)

    def call(self,vm,access):
        vm._call('edit',[access])
        frame=vm.call_stack[-1]
        return frame

    def test_load_store_and_return_update_caller_and_release_access(self):
        vm=self.vm([op('PRINT'),op('LOAD',0),op('PRINT'),op('PUSH',0),op('RETURN')])
        vm.memory[0]=1
        access=vm.value_accesses.begin(vm,vm.call_stack[0],0)
        self.call(vm,access)
        self.assertEqual(vm.run(),[2,2])
        self.assertEqual(vm.value_accesses.entries,{})
        with self.assertRaisesRegex(PixelError,'Expired'):
            vm.value_accesses.read(vm,access)

    def test_nested_path_write_preserves_alias_and_checkpoint_replay(self):
        vm=self.vm([op('PRINT'),op('LOAD',0),op('PRINT'),op('LOAD',1),op('PRINT'),op('PUSH',0),op('RETURN')])
        original=vm.allocate('int[]',[1,2],None)
        vm.memory[:]=[original,original]
        access=vm.value_accesses.begin(vm,vm.call_stack[0],0,[{'index':True}],[1])
        self.call(vm,access)
        checkpoint=vm.checkpoint()
        self.assertEqual(vm.run(),[3,[1,3],[1,2]])
        vm.restore_checkpoint(checkpoint)
        vm.collect()
        self.assertEqual(vm.run(),[3,[1,3],[1,2]])
        self.assertEqual(vm.value_accesses.entries,{})

    def test_nested_reborrow_suspends_parent_then_restores_access(self):
        vm=self.vm()
        vm.memory[0]=1
        parent=vm.value_accesses.begin(vm,vm.call_stack[0],0)
        outer=self.call(vm,parent)
        child=vm.value_accesses.begin(vm,outer,0)
        inner=self.call(vm,child)
        with self.assertRaisesRegex(PixelError,'suspended'):
            vm.value_accesses.write(vm,parent,7)
        vm.value_accesses.write(vm,child,9)
        vm.value_accesses.release_frame(inner)
        vm.call_stack.pop()
        self.assertEqual(vm.value_accesses.read(vm,parent),9)
        vm.value_accesses.write(vm,parent,5)
        self.assertEqual(vm.call_stack[0].memory[0],5)

    def test_same_root_and_direct_write_conflicts_are_rejected(self):
        vm=self.vm([op('PUSH',1),op('STORE',0),op('RETURN')])
        vm.memory[0]=1
        vm.value_accesses.begin(vm,vm.call_stack[0],0)
        with self.assertRaisesRegex(PixelError,'Overlapping'):
            vm.value_accesses.begin(vm,vm.call_stack[0],0)
        with self.assertRaisesRegex(PixelError,'exclusive inout'):
            vm.run()
        self.assertEqual(vm.memory[0],1)
        self.assertEqual(vm.value_accesses.entries,{})

    def test_checkpoint_restores_independent_locks_and_nested_borrow_lifetime(self):
        vm=self.vm()
        vm.memory[:]=[1,2]
        parent=vm.value_accesses.begin(vm,vm.call_stack[0],0)
        outer=self.call(vm,parent)
        child=vm.value_accesses.begin(vm,outer,0)
        self.call(vm,child)
        unrelated=vm.value_accesses.begin(vm,vm.call_stack[0],1)
        snapshot=vm.checkpoint()
        for _ in range(2):
            vm.restore_checkpoint(snapshot)
            root,outer,inner=vm.call_stack
            for slot in (0,1):
                with self.assertRaisesRegex(PixelError,'exclusive inout'):
                    vm.value_accesses.check_direct_write(vm,root,slot)
            with self.assertRaisesRegex(PixelError,'suspended'):
                vm.value_accesses.read(vm,parent)
            vm.value_accesses.discard_pending(vm,root,[unrelated])
            vm.value_accesses.check_direct_write(vm,root,1)
            with self.assertRaisesRegex(PixelError,'exclusive inout'):
                vm.value_accesses.check_direct_write(vm,root,0)
            vm.value_accesses.release_frame(outer)
            vm.value_accesses.check_direct_write(vm,root,0)
            for access in (parent,child,unrelated):
                with self.assertRaisesRegex(PixelError,'Expired'):
                    vm.value_accesses.read(vm,access)

    def test_failed_allocation_keeps_old_root_and_releases_call_access(self):
        vm=self.vm(max_heap_objects=1)
        original=vm.allocate('int[]',[1],None)
        vm.memory[0]=original
        access=vm.value_accesses.begin(vm,vm.call_stack[0],0,[{'index':True}],[0])
        self.call(vm,access)
        with self.assertRaisesRegex(PixelError,'Heap budget'):
            vm.run()
        self.assertEqual(vm.materialize(vm.call_stack[0].memory[0]),[1])
        self.assertEqual(vm.value_accesses.entries,{})

    def test_cancellation_releases_access(self):
        vm=self.vm()
        vm.memory[0]=1
        access=vm.value_accesses.begin(vm,vm.call_stack[0],0)
        self.call(vm,access)
        vm.cancel()
        with self.assertRaisesRegex(PixelError,'cancelled'):
            vm.run()
        self.assertEqual(vm.value_accesses.entries,{})
        self.assertEqual(vm.call_stack[0].memory[0],1)

    def test_invalid_paths_do_not_publish_locks(self):
        vm=self.vm()
        vm.memory[0]=vm.allocate('int[]',[1],None)
        for path,indices in [([{'index':True}],[8]),([None],[]),([{'index':True}],[]),([{'field':'bad'}],[])]:
            with self.subTest(path=path),self.assertRaises(PixelError):
                vm.value_accesses.begin(vm,vm.call_stack[0],0,path,indices)
            self.assertEqual(vm.value_accesses.entries,{})
        vm.memory[0]=vm.allocate('map<str,int>',{},None)
        with self.assertRaisesRegex(PixelError,'map key'):
            vm.value_accesses.begin(vm,vm.call_stack[0],0,[{'index':True}],['missing'])
        self.assertEqual(vm.value_accesses.entries,{})

    def test_abandoned_argument_releases_pending_access_before_next_write(self):
        vm=self.vm([op('UNWIND',1),op('PUSH',1),op('STORE',0),
                    op('LOAD',0),op('PRINT'),op('PUSH',0),op('RETURN')])
        vm.memory[0]=1
        vm.memory[1]=0
        vm.stack.append(vm.value_accesses.begin(vm,vm.call_stack[0],0))
        self.assertEqual(vm.run(),[9])
        self.assertEqual(vm.value_accesses.entries,{})

    def test_capture_and_storage_of_access_are_rejected(self):
        vm=self.vm([op('BIND',1),op('RETURN')])
        vm.memory[0]=1
        access=vm.value_accesses.begin(vm,vm.call_stack[0],0)
        inner=self.call(vm,access)
        with self.assertRaisesRegex(PixelError,'cannot be captured'):
            vm.capture_value(inner,0)
        vm.value_accesses.release_frame(inner)
        vm.call_stack.pop()
        vm.stack.append(access)
        with self.assertRaisesRegex(PixelError,'cannot be stored'):
            vm.run()

    def test_declared_callable_type_is_not_narrowed_to_initial_value(self):
        code=program([op('LOAD',0),op('RETURN')],typ='int',result='int')
        code['functions']['ordinary']=dict(params=['int'],locals=1,result='int',
                                          instructions=[op('LOAD',0),op('RETURN')])
        code['effectFunctions'].append('ordinary')
        vm=VM(code)
        vm.memory[0]=vm.allocate('fn(int)->(int)',{'$function':'subject'},None)
        access=vm.value_accesses.begin(vm,vm.call_stack[0],0,typ='effect_fn(int)->(int)')
        replacement=vm.allocate('effect_fn(int)->(int)',{'$function':'ordinary'},None)
        vm.value_accesses.write(vm,access,replacement)
        self.assertEqual(vm.value_accesses.read(vm,access),replacement)

    def test_debug_state_exposes_values_and_remains_json_serializable(self):
        import json
        vm=self.vm()
        vm.memory[0]=4
        access=vm.value_accesses.begin(vm,vm.call_stack[0],0)
        self.call(vm,access)
        self.assertEqual(vm.state()['memory'][0],4)
        json.dumps(vm.state())
        vm.cancel()
        with self.assertRaises(PixelError): vm.run()
        json.dumps(vm.state())
