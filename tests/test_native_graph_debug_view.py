"""Expression overlays inspect live and historical VM state without mutation."""
import unittest

from pixellang.flow_debug import flow_scopes, flow_values, inspect_flows
from pixellang.flow_debug_view import graph_view
from pixellang.model import PixelError
from tests.test_native_debug_values import timeline_for
from tests.test_expression_flow_bytecode import program, op


def named_program():
    code = program()
    child = code['flows']['child']
    child['values'] = [dict(node=40, name='updated', expression=4, slot=3, type='int')]
    child['order'] = [40]
    child['dependencies'] = [dict(source=1,target=40,reference=7),
        dict(source=2,target=40,reference=8),dict(source=40,target=3,reference=41),
        dict(source=2,target=6,reference=5)]
    code['functions']['child']['locals'] = 4
    code['functions']['child']['instructions'][5:5] = [op('STORE',3,40),op('LOAD',3,41)]
    return code


def advance_until(timeline, predicate):
    for _ in range(200):
        if predicate(timeline.vm): return
        if timeline.vm.halted: break
        timeline.advance()
    raise AssertionError('Expected pause point was not reached')


class NativeGraphDebugViewTests(unittest.TestCase):
    def test_named_computation_phases_and_paging(self):
        timeline = timeline_for(named_program())
        advance_until(timeline, lambda vm: vm.call_stack[-1].function == 'child')
        vm = timeline.vm
        self.assertEqual(flow_values(vm,0)['values']['updated']['phase'],'pending')
        advance_until(timeline, lambda vm: vm.call_stack[-1].pc == 3)
        self.assertEqual(flow_values(vm,0)['values']['updated']['phase'],'running')
        advance_until(timeline, lambda vm: vm.call_stack[-1].pc == 6)
        before = vm.checkpoint()
        view = graph_view(timeline,dict(z=timeline.cursor,frame=0,count=1))
        self.assertEqual(view['source'],'main.pxl')
        self.assertEqual(view['values'][0]['phase'],'complete')
        self.assertEqual(view['values'][0]['result']['value'],'5')
        self.assertEqual(view['totals'],dict(values=1,children=0,inputs=1,states=1))
        empty = graph_view(timeline,dict(z=timeline.cursor,frame=0,start=1))
        self.assertEqual(empty['values'],[])
        self.assertEqual(empty['inputs'],[])
        self.assertEqual(vm.checkpoint(),before)

    def test_children_have_explicit_identity_and_activation_status(self):
        timeline = timeline_for(program())
        advance_until(timeline,lambda vm: vm.call_stack[-1].function == 'parent')
        self.assertEqual(flow_values(timeline.vm,0)['children']['child']['phase'],'pending')
        advance_until(timeline,lambda vm: vm.call_stack[-1].function == 'parent' and vm.call_stack[-1].pc == 7)
        self.assertEqual(flow_values(timeline.vm,0)['children']['child']['phase'],'skipped')
        advance_until(timeline,lambda vm: vm.call_stack[-1].function == 'child')
        parent = flow_values(timeline.vm,1)
        self.assertEqual(parent['children']['child']['phase'],'running')
        self.assertEqual(parent['children']['child']['path'],[20])
        self.assertEqual(inspect_flows(timeline.vm)[-1]['path'],[20])
        advance_until(timeline,lambda vm: vm.call_stack[-1].function == 'parent')
        self.assertEqual(flow_values(timeline.vm,0)['children']['child']['phase'],'complete')

    def test_ordinary_helper_inherits_child_scope_without_instance_path(self):
        code = named_program()
        code['functions']['child']['instructions'][4] = op('CALL','add',4)
        code['functions']['add'] = dict(params=['int','int'],locals=2,result='int',
            instructions=[op('LOAD',0),op('LOAD',1),op('ADD'),op('RETURN')])
        timeline = timeline_for(code)
        advance_until(timeline,lambda vm: vm.call_stack[-1].function == 'add')
        before = timeline.vm.checkpoint()
        scopes = {item['frame']:item for item in flow_scopes(timeline.vm)}
        self.assertEqual(scopes[0]['path'],['flow',1])
        self.assertEqual(scopes[1]['path'],['flow',1])
        self.assertEqual(scopes[2]['path'],['flow',2])
        self.assertEqual(flow_values(timeline.vm,1)['path'],[20])
        self.assertEqual(flow_values(timeline.vm,1)['values']['updated']['phase'],'running')
        with self.assertRaisesRegex(PixelError,'not a native flow'): flow_values(timeline.vm,0)
        self.assertEqual(timeline.vm.checkpoint(),before)

    def test_historical_staged_values_and_read_only_inspection(self):
        timeline = timeline_for(named_program())
        advance_until(timeline,lambda vm: vm.flow_runtime.transaction and vm.flow_runtime.transaction.staged)
        selected = timeline.cursor
        while not timeline.vm.halted: timeline.advance()
        timeline.seek(selected)
        before = timeline.replay_vm.checkpoint()
        view = graph_view(timeline,dict(z=selected,frame=0))
        self.assertEqual(view['step'],1)
        self.assertEqual(view['states'][0]['old']['value'],'0')
        self.assertEqual(view['states'][0]['next']['value'],'5')
        self.assertTrue(view['states'][0]['staged'])
        self.assertEqual(view['values'][0]['result']['value'],'5')
        self.assertEqual(timeline.replay_vm.checkpoint(),before)
        self.assertEqual(timeline.vm.flow_runtime.instances[0].steps,4)
        with self.assertRaisesRegex(PixelError,'different time'):
            graph_view(timeline,dict(z=selected+1,frame=0))

    def test_retained_state_is_visible_during_live_and_historical_steps(self):
        code=program()
        code['flows']['child']['updates']=[]
        code['flows']['child']['dependencies']=[dict(source=2,target=6,reference=5)]
        del code['functions']['child']['instructions'][2:6]
        timeline=timeline_for(code)
        advance_until(timeline,lambda vm: vm.call_stack[-1].function=='child')
        selected=timeline.cursor
        before=timeline.vm.checkpoint()
        view=graph_view(timeline,dict(z=selected,frame=0))
        state=view['states'][0]
        self.assertTrue(state['retained'])
        self.assertFalse(state['staged'])
        self.assertEqual(state['old']['value'],'0')
        self.assertEqual(state['next']['value'],'0')
        self.assertEqual(timeline.vm.checkpoint(),before)
        while not timeline.vm.halted:timeline.advance()
        timeline.seek(selected)
        self.assertEqual(graph_view(timeline,dict(z=selected,frame=0)),view)
        self.assertEqual(timeline.replay_vm.checkpoint(),before)

    def test_invalid_page_and_frame(self):
        timeline = timeline_for(program())
        advance_until(timeline,lambda vm: vm.call_stack[-1].function == 'parent')
        for page in [dict(start=-1),dict(count=0),dict(count=101),dict(count=True)]:
            with self.assertRaisesRegex(PixelError,'Invalid graph page'):
                graph_view(timeline,dict(z=timeline.cursor,frame=0,**page))
        with self.assertRaises(PixelError):
            graph_view(timeline,dict(z=timeline.cursor,frame=True))

    def test_completed_child_status_is_unknown_after_parent_fault(self):
        code = program()
        code['functions']['main']['instructions'][3]['arg'] = 3
        code['functions']['parent']['instructions'][-3:-3] = [op('PUSH',1),op('DIV')]
        timeline = timeline_for(code)
        while not timeline.vm.halted: timeline.advance()
        self.assertIsNone(timeline.vm.flow_runtime.transaction)
        # Aborting a transaction discards activation history. Do not report a
        # successful commit, or falsely classify the executed child as pending.
        self.assertEqual(flow_values(timeline.vm,0)['children']['child']['phase'],'unavailable')
        self.assertEqual(timeline.vm.flow_runtime.instances[0].cells[((20,),2)],0)

    def test_failed_computation_keeps_old_state_and_reports_failure(self):
        code = named_program()
        instructions = code['functions']['child']['instructions']
        instructions[4:5] = [op('PUSH',1),op('DIV',node=4)]
        timeline = timeline_for(code)
        while not timeline.vm.halted: timeline.advance()
        before = timeline.vm.checkpoint()
        self.assertIsNotNone(timeline.vm.error)
        self.assertEqual(flow_values(timeline.vm,0)['values']['updated']['phase'],'failed')
        self.assertEqual(flow_values(timeline.vm,1)['children']['child']['phase'],'failed')
        self.assertEqual(flow_values(timeline.vm,0)['oldState']['n'],0)
        self.assertFalse(flow_values(timeline.vm,0)['nextState']['n']['staged'])
        self.assertEqual(timeline.vm.checkpoint(),before)


if __name__ == '__main__': unittest.main()
