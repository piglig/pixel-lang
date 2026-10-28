"""Native Result/Option propagation through source, IR, PNG and module reuse."""
from tests.native_compiler import native_service
import unittest

from pixellang.vm import VM


SOURCE = '''enum Failure { Rejected(message: string) }
fn number(ok: bool) -> Result[int, Failure] {
 if ok { return .Ok(7) }
 return .Err(.Rejected("declined"))
}
fn describe(ok: bool) -> Result[string, Failure] {
 let value = 10 + number(ok)?
 return .Ok(string(value))
}
fn optional(ok: bool) -> Option[int] {
 if ok { return Option.Some(3) }
 return Option.None
}
fn optionalText(ok: bool) -> Option[string] = Option.Some(string(optional(ok)? + 1))
effect fn main() {
 print(describe(true) == Result::[string,Failure].Ok("17"))
 print(describe(false) == Result::[string,Failure].Err(.Rejected("declined")))
 print(optionalText(true) == Option.Some("4"))
 print(optionalText(false) == Option::[string].None)
}
'''


class NativePropagationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)

    def request(self, operation, **args):
        response = self.service.request(dict(version=1, operation=operation, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def execute(self, source, **modules):
        code = self.request('compile', files={'main.pxl': source, **modules})['bytecode']
        vm = VM(code)
        output = vm.run()
        self.assertEqual(vm.value_accesses.entries, {})
        return output

    def test_native_result_needs_no_import_and_changes_success_type(self):
        self.assertEqual(self.execute(SOURCE), [True]*4)

    def test_single_evaluation_short_circuit_and_pending_operands(self):
        self.assertEqual(self.execute('''
effect fn number(ok: bool) -> Result[int,string] {
 print("number")
 if ok {return .Ok(2)}
 return .Err("failed")
}
effect fn suffix() -> int {print("suffix");return 3}
fn sum(a:int,b:int,c:int)->int=a+b+c
effect fn evaluate(ok: bool) -> Result[int,string] = .Ok(sum(10,number(ok)?,suffix()))
effect fn main(){
 print(evaluate(false)==Result::[int,string].Err("failed"))
 print(evaluate(true)==Result::[int,string].Ok(15))
}
'''), ['number',True,'number','suffix',True])

    def test_generic_success_and_error_types_are_substituted(self):
        self.assertEqual(self.execute('''
fn wrap[T,E](value:Result[T,E])->Result[[T],E] = .Ok([value?])
fn fallback[T](value:Option[T])->Option[[T]] = Option.Some([value?])
effect fn main(){
 print(wrap(Result::[int,string].Ok(4))==Result::[[int],string].Ok([4]))
 print(wrap(Result::[string,int].Err(9))==Result::[[string],int].Err(9))
 print(fallback(Option.Some(5))==Option.Some([5]))
 print(fallback(Option::[string].None)==Option::[[string]].None)
}
'''), [True]*4)

    def test_lambda_has_its_own_propagation_boundary(self):
        self.assertEqual(self.execute('''
fn outer()->Result[int,int]{
 let input: Result[int,string] = .Err("inner")
 let action = fn() -> Result[string,string] = .Ok(string(input?))
 let result = action()
 match result {
  .Ok(value)=>{return .Ok(1)},
  .Err(error)=>{return .Ok(2)}
 }
}
fn apply(action:fn()->Result[int,string])->Result[int,string]=action()
effect fn main(){
 print(outer()==Result::[int,int].Ok(2))
 let input: Result[int,string] = .Err("contextual")
 print(apply(fn() = .Ok(input?))==Result::[int,string].Err("contextual"))
}
'''), [True,True])

    def test_early_return_releases_pending_inout_without_rollback(self):
        self.assertEqual(self.execute('''
fn assign(target:inout int,value:int){target=value}
fn edit(target:inout int,input:Result[int,string])->Result[int,string]{
 target+=1
 assign(&target,input?)
 return .Ok(target)
}
effect fn main(){
 var value=1
 let failed=edit(&value,Result::[int,string].Err("no"))
 print(failed==Result::[int,string].Err("no"));print(value)
 assign(&value,8)
 let accepted=edit(&value,Result::[int,string].Ok(12))
 print(accepted==Result::[int,string].Ok(12));print(value)
}
'''), [True,2,True,12])

    def test_invalid_operand_family_error_and_boundary_are_rejected(self):
        cases = [
            ('fn f()->Result[int,string]=.Ok(1?)', 'native Result or Option'),
            ('fn f(v:Result[int,string])->int=v?', 'same Result or Option family'),
            ('fn f(v:Option[int])->Result[int,string]=.Ok(v?)', 'same Result or Option family'),
            ('fn f(v:Result[int,int])->Result[int,string]=.Ok(v?)', 'same error type'),
            ('enum Outcome {Ok(value:int),Err(error:string)}\nfn f(v:Outcome)->Result[int,string]=.Ok(v?)', 'native Result or Option'),
            ('fn f(v:Result[int,string])->Result[int,string]{let action=fn() = v?;return .Ok(1)}', 'known function return type'),
            ('flow F() step(v:Result[int,string])->Result[int,string]{output .Ok(v?)}', 'enclosing function'),
        ]
        for declaration, message in cases:
            with self.subTest(declaration=declaration):
                response = self.service.request(dict(version=1,operation='compile',files={
                    'main.pxl':declaration+'\neffect fn main(){}'}))
                self.assertNotEqual(response['status'],'ok',response)
                self.assertIn(message,str(response))

    def test_native_result_is_always_must_use(self):
        response=self.service.request(dict(version=1,operation='compile',files={
            'main.pxl':'effect fn main(){Result::[int,string].Ok(1)}'}))
        self.assertNotEqual(response['status'],'ok',response)
        self.assertIn('Must-use value cannot be discarded',str(response))

    def test_formatting_png_execution_and_recovered_source(self):
        files={'main.pxl':SOURCE}
        formatted=self.request('format',files=files)['text']
        self.assertIn('?',formatted)
        self.assertEqual(self.request('format',files={'main.pxl':formatted})['text'],formatted)
        image=self.request('export-png',files=files)['image']
        self.assertEqual(VM(self.request('compile-png',image=image)['bytecode']).run(),[True]*4)
        recovered=self.request('recover-png',image=image)
        self.assertTrue(any('?' in source for source in recovered['files'].values()))
        self.assertEqual(VM(self.request('compile',**recovered)['bytecode']).run(),[True]*4)

    def test_incremental_ir_relocates_propagation_payload_types(self):
        files={
            'domain.pxl':'export enum Failure {Denied}\nexport fn f(v:Result[int,Failure])->Result[string,Failure]=.Ok(string(v?))',
            'main.pxl':'import "domain.pxl" as domain\neffect fn main(){print(domain.f(Result::[int,domain.Failure].Err(.Denied))==Result::[string,domain.Failure].Err(.Denied))}',
        }
        self.assertEqual(VM(self.request('compile',files=files,incremental=True)['bytecode']).run(),[True])
        files['earlier.pxl']='export record Extra {value:int}'
        files['main.pxl']='import "earlier.pxl" as earlier\n'+files['main.pxl']
        cached=self.request('compile',files=files,incremental=True)['bytecode']
        fresh=self.request('compile',files=files,incremental=False)['bytecode']
        self.assertEqual(VM(cached).run(),[True])
        self.assertEqual(cached,fresh)


if __name__=='__main__':
    unittest.main()
