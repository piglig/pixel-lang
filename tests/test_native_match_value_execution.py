"""Native checking and execution of match value blocks."""
from tests.native_compiler import native_service
import unittest

from pixellang.vm import VM


class NativeMatchValueExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)

    def compile(self, source):
        return self.service.request(dict(version=1, operation='compile',
                                        files={'main.pxl': source}, incremental=False))

    def output(self, source, expected):
        response = self.compile(source)
        self.assertEqual(response['status'], 'ok', response)
        code = response['result']['bytecode']
        self.assertEqual(VM(code).run(), expected)
        return code

    def test_local_computation_and_branch_selection_share_surrounding_frame(self):
        code = self.output('''fn value(input:Option[int])->int = match input {
 Option.Some(n)=>{var result=n;result+=2;result*3},
 Option.None=>{let fallback=4;fallback}
}
effect fn main(){print(value(.Some(2)));print(value(.None))}''', [12, 4])
        self.assertEqual(len(code['functions']), 2)

    def test_nested_match_tail_and_local_loop_control(self):
        self.output('''fn value(input:Option[int])->int = match input {
 Option.Some(n)=>{
  var total=0
  for item in [1,2,3,4] {
   if item==2 {continue}
   if item==4 {break}
   total+=item
  }
  match input {Option.Some(x)=>{x+total},Option.None=>0}
 },
 Option.None=>0
}
effect fn main(){print(value(.Some(3)))}''', [7])

    def test_expected_nominal_type_reaches_block_tail(self):
        self.output('''fn value(input:Option[int])->Option[int] = match input {
 Option.Some(n)=>{let doubled=n*2;.Some(doubled)},Option.None=>{.None}
}
effect fn main(){print(match value(.Some(3)){Option.Some(n)=>n,Option.None=>0})}''', [6])

    def test_explicit_discard_and_empty_block_have_unit_result(self):
        self.output('''effect fn report(input:Option[int]) = match input {
 Option.Some(n)=>{print(n);},Option.None=>{}
}
effect fn main(){report(.Some(7));report(.None)}''', [7])

    def test_closure_captures_block_local_value(self):
        self.output('''fn callback(input:Option[int])->fn()->int = match input {
 Option.Some(n)=>{let value=n+1;(fn()->int=value)},
 Option.None=>{(fn()->int=0)}
}
effect fn main(){let f=callback(.Some(8));print(f())}''', [9])

    def test_blocks_preserve_type_scope_effect_and_must_use_diagnostics(self):
        cases = [
            ('fn f(v:Option[int])->int=match v {Option.Some(n)=>{n;},Option.None=>0}', 'Type mismatch'),
            ('fn f(v:Option[int])->int=match v {Option.Some(n)=>{let x=n;x},Option.None=>x}', 'Unknown name'),
            ('fn f(v:Option[int])->int=match v {Option.Some(n)=>{print(n);n},Option.None=>0}', 'effect'),
            ('fn f(v:Option[int])->int=match v {Option.Some(n)=>{parseInt("1");n},Option.None=>0}', 'Must-use'),
            ('fn f(v:Option[int])->int=match v {Option.Some(n)=>{n=2;n},Option.None=>0}', 'immutable'),
        ]
        for source, message in cases:
            with self.subTest(message=message):
                response = self.compile(source+'\neffect fn main(){}')
                self.assertEqual(response['status'], 'diagnostics', response)
                self.assertTrue(any(message.lower() in str(item).lower() for item in response['diagnostics']), response)

    def test_propagating_arguments_do_not_hide_static_errors(self):
        cases = [
            ('''fn pair(left:int,right:int)->int=left+right
fn choose()->Option[int]=.Some(pair("wrong",Option::[int].None?))''', 'Expected int'),
            ('''fn pair(left:int,right:int)->int=left+right
fn choose()->Option[int]=.Some(pair(Option::[int].None?,"wrong"))''', 'Expected int'),
            ('''fn same[T](a:T,b:T,c:T)->T=a
fn choose()->Option[int]{let ignored=same(1,Option::[int].None?,"wrong");return .Some(0)}''', 'conflict'),
            ('''effect fn mark()->int{print(1);return 1}
fn pair(left:int,right:int)->int=left+right
fn choose()->Option[int]=.Some(pair(mark(),Option::[int].None?))''', 'effect'),
        ]
        for source, message in cases:
            with self.subTest(source=source):
                response = self.compile(source+'\neffect fn main(){}')
                self.assertEqual(response['status'], 'diagnostics', response)
                self.assertTrue(any(message.lower() in str(item).lower() for item in response['diagnostics']), response)


if __name__ == '__main__':
    unittest.main()
