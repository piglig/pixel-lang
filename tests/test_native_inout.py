"""Native inout programs execute through scoped bytecode accesses."""
from tests.native_compiler import native_service
import unittest
from pixellang.vm import VM


class NativeInoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler=native_service()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, source):
        return self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))

    def execute(self, source):
        response=self.compile(source)
        self.assertEqual(response['status'],'ok',response)
        vm=VM(response['result']['bytecode'])
        output=vm.run()
        self.assertEqual(vm.value_accesses.entries,{})
        return output

    def test_generic_replacement_and_value_container_updates(self):
        self.assertEqual(self.execute('''fn replace[T](value: inout T, next: T) { value=next }
fn edit(values: inout [int]) { values.append(3); values[0]+=4 }
effect fn main() {
 var n=1
 replace(&n,7)
 let old=[1,2]
 var values=old
 edit(&values)
 print(n); print(old); print(values)
}'''),[7,[1,2],[5,2,3]])

    def test_nested_address_evaluates_once_and_preserves_snapshot(self):
        self.assertEqual(self.execute('''record Box { values: [int] }
effect fn index() -> int { print("index"); return 0 }
fn edit(value: inout int) { value+=5 }
effect fn main() {
 let old=[Box{values:[1]}]
 var changed=old
 edit(&changed[index()].values[0])
 print(old[0].values); print(changed[0].values)
}'''),['index',[1],[6]])

    def test_higher_order_lambda_and_nested_reborrows(self):
        self.assertEqual(self.execute('''fn apply(value: inout int, action: fn(inout int)) { action(&value) }
fn add(value: inout int, amount: int) { value+=amount }
fn twice(value: inout int) { add(&value,value); add(&value,value) }
effect fn main() {
 var value=2
 apply(&value,fn(item: inout int) { item+=1 })
 twice(&value)
 print(value)
}'''),[12])

    def test_inout_cannot_escape_or_overlap(self):
        for source in [
            'fn edit(x: inout int) {}\neffect fn main(){var x=1;edit(x)}',
            'fn edit(x: inout int) {}\neffect fn main(){let x=1;edit(&x)}',
            'fn pair(x: inout int,y: inout int) {}\neffect fn main(){var x=1;pair(&x,&x)}',
            'fn edit(x: inout int){let f=fn() -> int = x}\neffect fn main(){}',
        ]:
            with self.subTest(source=source): self.assertNotEqual(self.compile(source)['status'],'ok')

    def test_nested_access_finishes_before_later_argument_reserves_same_root(self):
        source='''fn next(value: inout int) -> int {value+=1;return value}
fn assign(value: int, target: inout int) {target=value+1}
fn optional[T](value: Option[T], target: inout T) {
 match value {Option.Some(found)=>{target=found},Option.None=>{}}
}
effect fn main(){
 var value=0
 assign(next(&value),&value)
 print(value)
 optional(Option.Some(next(&value)),&value)
 print(value)
}
'''
        result=self.compile(source)
        self.assertEqual(result['status'],'ok',result)
        self.assertEqual(VM(result['result']['bytecode']).run(),[2,3])

    def test_earlier_reserved_argument_rejects_later_nested_write_in_any_inference_pass(self):
        for expression in ('next(&value)','Option.Some(next(&value))'):
            parameter='int' if expression.startswith('next') else 'Option[int]'
            source='''fn next(value: inout int)->int {value+=1;return value}
fn use(target: inout int, value: PARAMETER) {}
fn main(){var value=0;use(&value,EXPRESSION)}
'''.replace('PARAMETER',parameter).replace('EXPRESSION',expression)
            result=self.compile(source)
            self.assertNotEqual(result['status'],'ok',result)
            self.assertIn('Overlapping value writes',str(result))
