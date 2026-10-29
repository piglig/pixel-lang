"""Native construction and typed instance methods replace global flow APIs."""
from tests.native_compiler import native_service
import unittest
from unittest.mock import patch
from pixellang.native_events import NativeApplicationRuntime
from pixellang.native_editor import NativeSymbolIndex
from pixellang.vm import VM

COUNTER = '''flow Counter(initial:int) step(delta:int)->int {
 state total=initial
 next total=total+delta
 output total
}
'''
PROGRAM = COUNTER + '''effect fn main(){
 let first=Counter(10)
 let second=Counter(1)
 print(first.step(2))
 print(first.step(3))
 print(second.step(9))
 print(first.step(0))
}'''

class NativeUnifiedFlowAPITests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
     cls.compiler=native_service()
     cls.addClassCleanup(cls.compiler.close)
 def request(self,operation,**args):
  with patch('pixellang.text.parse_text',side_effect=AssertionError('host parser forbidden')):
   response=self.compiler.request(dict(version=1,operation=operation,incremental=False,**args))
  self.assertEqual(response['status'],'ok',response)
  return response['result']
 def compile(self,source):return self.request('compile',files={'main.pxl':source})['bytecode']
 def test_constructor_and_steps_preserve_independent_state_through_png(self):
  self.assertEqual(VM(self.compile(PROGRAM)).run(),[10,12,1,15])
  image=self.request('export-png',files={'main.pxl':PROGRAM})['image']
  self.assertEqual(VM(self.request('compile-png',image=image)['bytecode']).run(),[10,12,1,15])
  recovered=self.request('recover-png',image=image)
  self.assertTrue(any(' step(' in source for source in recovered['files'].values()))
  self.assertEqual(VM(self.request('compile',**recovered)['bytecode']).run(),[10,12,1,15])
 def test_constructor_signature_uses_creation_parameters(self):
  source=COUNTER+'effect fn main(){let value=Counter('
  files={'main.pxl':source}
  index=NativeSymbolIndex(files,'main.pxl',self.request('editor-index',files=files)['index'])
  signature=index.signature_help('main.pxl',len(source))
  self.assertEqual(signature['parameters'],['initial: int'])
  self.assertIn('Counter(initial: int) -> Flow[',signature['label'])
  hover=index.hover('main.pxl',source.index('Counter')+1)
  self.assertEqual(hover['label'],'flow Counter(initial: int) step(delta: int) -> int')
 def test_persistent_children_use_the_same_creation_and_step_forms(self):
  source=COUNTER+'''flow Parent(initial:int) step(delta:int)->int {
   instance child=Counter(initial)
   output child.step(delta)
  }
  effect fn main(){let parent=Parent(7);print(parent.step(2));print(parent.step(3))}'''
  self.assertEqual(VM(self.compile(source)).run(),[7,9])
 def test_stateless_flow_calls_construct_instead_of_evaluating_a_step(self):
  source='flow Echo() step(value:int)->int {output value}\neffect fn main(){let echo=Echo();print(echo.step(5))}'
  self.assertEqual(VM(self.compile(source)).run(),[5])
  response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source.replace('let echo=Echo();print(echo.step(5))','print(Echo(5))')}))
  self.assertEqual(response['status'],'diagnostics',response)
  self.assertTrue(any('constructor argument count' in item['message'] for item in response['diagnostics']))
 def test_constructor_step_types_and_pure_effect_boundaries_are_checked(self):
  cases=[('effect fn main(){let c=Counter("bad")}', 'Expected int'),
         ('effect fn main(){let c=Counter(0);c.step("bad")}', 'Expected int'),
         ('fn bad(){let c=Counter(0)}\neffect fn main(){}','effect'),
         ('fn bad(c:Flow[fn(int)->int])->int=c.step(1)\neffect fn main(){}','effect')]
  for body,expected in cases:
   with self.subTest(body=body):
    response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':COUNTER+body},incremental=False))
    self.assertEqual(response['status'],'diagnostics',response)
    self.assertTrue(any(expected.lower() in item['message'].lower() for item in response['diagnostics']),response)
 def test_retired_globals_are_not_compatibility_aliases(self):
  for call in ('flowNew(Counter,0)','flowStep(Counter(0),1)','flowRun(Counter(0),1,1)','spaceNew::[World,int,int]("x")'):
   response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':'frame World=meters\n'+COUNTER+'effect fn main(){'+call+'}'},incremental=False))
   self.assertEqual(response['status'],'diagnostics',response)
   self.assertTrue(any(item['phase']=='bind' for item in response['diagnostics']),response)
 def test_space_constructor_is_native_in_function_and_state_initializer(self):
  source='''frame World=meters
  flow Scene() step(delta:int)->int {
   state world=Space::[World,int,string]("state")
   output world.size()+delta
  }
  effect fn main(){
   let space=Space::[World,int,string]("main")
   let next=space.insert("entity-0", Point3::[World]{x:0.0,y:0.0,z:1.0},7)
   print(space.size());print(next.size());print(Scene().step(3))
  }'''
  self.assertEqual(VM(self.compile(source)).run(),[0,1,3])
 def test_host_schedules_initialized_flow(self):
  source='''record Duration {nanoseconds:int}
  record Clock {index:int,elapsed:Duration,delta:Duration}
  enum Event {Tick}
  flow Application() step(event:Event,clock:Clock)->int {
   state count=0
   next count=count+1
   output count
  }
  effect fn main(){Application()}'''
  runtime=NativeApplicationRuntime(VM(self.compile(source)), configuration={'intervalNanoseconds':10})
  self.assertEqual(runtime.step(),0)
  self.assertEqual(runtime.step(),1)

if __name__=='__main__':unittest.main()
