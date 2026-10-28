from types import SimpleNamespace
import unittest
from pixellang.workstation import Workstation
from pixellang.flow_debug_view import frame_view
from pixellang.vm import VM
from pixellang.model import PixelError
from tests.test_native_flow_debug import program
from tests.test_expression_flow_bytecode import program as nested
class FrameMetadataTests(unittest.TestCase):
 def test_helper_frame_owners_history_and_readonly(self):
  live=VM(program());historical=VM(program())
  for _ in range(4):historical.step(snapshot=False)
  t=SimpleNamespace(cursor=1,events=[{},{}],vm=live,replay_vm=historical,compiled=SimpleNamespace(debug={'functions':{'flow':{'source':'main.pxl'}}}))
  before=historical.checkpoint();expected={'z':1,'frames':[{'frame':1,'source':'main.pxl','declaration':5,'name':'Pipeline'}]}
  w=Workstation.__new__(Workstation);w.sessions={'test':t}
  self.assertEqual(w.request('flow-frames',{'session':'test','z':1}),expected)
  self.assertEqual(historical.checkpoint(),before)
  with self.assertRaisesRegex(PixelError,'different time'):frame_view(t,{'z':2})
  t.cursor=2;self.assertEqual(frame_view(t,{'z':2}),{'z':2,'frames':[]})
 def test_nested_frames_are_innermost_first(self):
  vm=VM(nested())
  while vm.call_stack[-1].function!='child':vm.step(snapshot=False)
  t=SimpleNamespace(cursor=0,events=[],vm=vm,compiled=SimpleNamespace(debug={'functions':{k:{'source':'main.pxl'} for k in vm.bytecode['flows']}}))
  before=vm.checkpoint();rows=frame_view(t,{'z':0})['frames']
  self.assertEqual([r['name'] for r in rows],['Child','Parent']);self.assertEqual([r['frame'] for r in rows],[0,1]);self.assertEqual(vm.checkpoint(),before)
if __name__ == '__main__':
 unittest.main()
