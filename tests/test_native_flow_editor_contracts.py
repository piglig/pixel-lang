"""Studio displays the native flow receiver contract, including effects."""
from tests.native_compiler import native_service
import unittest
from pixellang.native_editor import NativeSymbolIndex

PREFIX='''flow Counter(initial:int) step(delta:int)->int {
 state value=initial
 next value=value+delta
 output value
}
effect fn main(){let counter=Counter(0);
'''
class NativeFlowEditorContractTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
     cls.compiler=native_service()
     cls.addClassCleanup(cls.compiler.close)
 def index(self,files):
  response=self.compiler.request(dict(version=1,operation='editor-index',files=files))
  self.assertEqual(response['status'],'ok',response)
  return NativeSymbolIndex(files,'main.pxl',response['result']['index'])
 def complete(self,text):
  index=self.index({'main.pxl':text})
  return index.complete('main.pxl',len(text),analyze=self.index)
 def test_unfinished_receiver_has_effectful_step_signature(self):
  items=self.complete(PREFIX+'counter.')
  step=next(item for item in items if item['label']=='step')
  self.assertEqual(step,dict(label='step',kind='Method',detail='effect fn step(input1: int) -> int'))
  self.assertFalse(any(item['label']=='run' for item in items))
 def test_unfinished_step_call_and_hover_share_native_signature(self):
  text=PREFIX+'counter.step('
  index=self.index({'main.pxl':text})
  signature=index.signature_help('main.pxl',len(text))
  self.assertEqual(signature['label'],'effect fn step(input1: int) -> int')
  self.assertEqual(signature['parameters'],['input1: int'])
  text=PREFIX+'print(counter.step(2))}'
  index=self.index({'main.pxl':text})
  self.assertEqual(index.hover('main.pxl',text.index('counter.step')+len('counter.'))['label'],signature['label'])
 def test_clock_contract_does_not_add_language_methods(self):
  prefix='''record Duration{nanoseconds:int}
record Clock{index:int,elapsed:Duration,delta:Duration}
enum Event{Tick}
flow App() step(event:Event,clock:Clock)->int {output clock.index}
effect fn main(){let app=App();'''
  items=self.complete(prefix+'app.')
  self.assertFalse(any(item['label']=='run' for item in items))
  self.assertTrue(any(item['label']=='step' for item in items))
  invalid=prefix.replace('elapsed:Duration,delta:Duration','elapsed:int,delta:int')
  self.assertFalse(any(item['label']=='run' for item in self.complete(invalid+'app.')))

if __name__=='__main__':unittest.main()
