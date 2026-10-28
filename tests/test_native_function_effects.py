"""Native function effects survive IR lowering and streamed bytecode assembly."""
from tests.native_compiler import native_service
import unittest
from pixellang.vm import VM

class NativeFunctionEffectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler=native_service()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, source):
        return self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))

    def execute(self, source):
        response=self.compile(source)
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        self.assertIn('effectFunctions',code)
        self.assertNotIn('componentFunctions',code)
        return VM(code).run(),code

    def test_effectful_calls_and_pure_generic_callback(self):
        output,code=self.execute('''fn identity[T](value: T) -> T = value
effect fn apply[T](value: T, callback: effect fn(T)->T) -> T = callback(value)
effect fn main() { print(apply(7,identity::[int])) }
''')
        self.assertEqual(output,[7])
        self.assertGreaterEqual(len(code['effectFunctions']),2)

    def test_effectful_closure_keeps_value_capture_and_type(self):
        output,code=self.execute('''fn make(value: int) -> effect fn() = effect fn() { print(value) }
effect fn main() {
 var value=3
 let callback=make(value)
 value=9
 callback()
}
''')
        self.assertEqual(output,[3])
        self.assertTrue(any('effect_fn' in fn['result'] for fn in code['functions'].values()))

    def test_pure_inout_function_and_nested_record_update(self):
        output,_=self.execute('''record Item { value: int }
fn edit(value: inout Item) { value=value with { value: value.value+1 } }
effect fn main() {
 let before=Item{value:1}
 var after=before
 edit(&after)
 print(before.value); print(after.value)
}
''')
        self.assertEqual(output,[1,2])

    def test_pure_flow_node_executes_without_component_boundary(self):
        output,_=self.execute('''fn twice(value: int) -> int = value*2
flow Pipeline() step(value: int) -> int {
 let operation=twice(value)
 output operation
}
effect fn main() { print(Pipeline().step(4)) }
''')
        self.assertEqual(output,[8])

    def test_rejects_undeclared_effects_and_effect_erasure(self):
        for source in ('fn main() { print(1) }',
                       'effect fn external() {}\nfn main() { external() }',
                       'effect fn external() {}\neffect fn main() { let callback: fn()=external }'):
            with self.subTest(source=source):
                self.assertNotEqual(self.compile(source)['status'],'ok')

    def test_space_initialization_runs_under_flow_construction_permission(self):
        for constructor,identity,arguments in (('()', '"scene"', ''),
                                                ('(name:string)', 'name', '"scene"')):
            with self.subTest(constructor=constructor):
                source='''frame World=meters
flow SceneCONSTRUCTOR step()->Space[World,int,bool] {
 state world:Space[World,int,bool]=Space::[World,int,bool](IDENTITY)
 next world=world
 output world
}
effect fn main(){
 let first=Scene(ARGUMENTS)
 let second=Scene(ARGUMENTS)
 let before=first.step()
 let changed=before.insert(Point3::[World]{x:0.0,y:0.0,z:1.0}, 7)
 print(before.size());print(changed.size())
 print(first.step().size());print(second.step().size())
}
'''.replace('CONSTRUCTOR',constructor).replace('IDENTITY',identity).replace('ARGUMENTS',arguments)
                output,_=self.execute(source)
                self.assertEqual(output,[0,1,0,0])

    def test_space_initializer_permission_does_not_allow_other_external_effects(self):
        pure='''frame World=meters
fn create()->Space[World,int,bool]=Space::[World,int,bool]("scene")
fn main(){}'''
        result=self.compile(pure)
        self.assertNotEqual(result['status'],'ok')
        self.assertIn('Pure function cannot call an external effect: space-new',str(result))
        for identity in ('input()', 'readText("name.txt")'):
            result=self.compile('''frame World=meters
flow Scene() step()->Space[World,int,bool] {
 state world:Space[World,int,bool]=Space::[World,int,bool](IDENTITY)
 next world=world
 output world
}
effect fn main(){let instance=Scene()}'''.replace('IDENTITY',identity))
            self.assertNotEqual(result['status'],'ok')
            self.assertIn('Flow initializer requires',str(result))

    def test_studio_palette_contains_only_pure_callable_targets(self):
        source = """fn twice(value: int) -> int = value*2
effect fn external(value: int) -> int = value
flow Pipeline() step(value: int) -> int {
 let operation=twice(value)
 output operation
}
effect fn main() { print(Pipeline().step(4)) }
"""
        response=self.compiler.request(dict(version=1,operation='native-graphs',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        palette=response['result']['projection']['graphs'][0]['palette']
        targets={item['target']:item['kind'] for item in palette}
        self.assertEqual(targets['twice'],'fn')
        self.assertIn('Pipeline',targets)
        self.assertNotIn('external',targets)
        self.assertNotIn('main',targets)


class NativeFunctionEffectPNGTests(unittest.TestCase):
    setUpClass = classmethod(NativeFunctionEffectTests.setUpClass.__func__)
    def test_space_state_initialization_survives_png_and_source_recovery(self):
        source='''frame World=meters
flow Scene(name:string) step()->Space[World,int,bool] {
 state world:Space[World,int,bool]=Space::[World,int,bool](name)
 next world=world
 output world
}
effect fn main(){
 let first=Scene("scene")
 let second=Scene("scene")
 print(first.step().size()); print(second.step().size())
}
'''
        exported=self.compiler.request(dict(version=1,operation='export-png',files={'main.pxl':source}))
        self.assertEqual(exported['status'],'ok',exported)
        image=exported['result']['image']
        compiled=self.compiler.request(dict(version=1,operation='compile-png',image=image))
        self.assertEqual(compiled['status'],'ok',compiled)
        self.assertEqual(VM(compiled['result']['bytecode']).run(),[0,0])
        recovered=self.compiler.request(dict(version=1,operation='recover-png',image=image))
        self.assertEqual(recovered['status'],'ok',recovered)
        rebuilt=self.compiler.request(dict(version=1,operation='compile',**recovered['result']))
        self.assertEqual(rebuilt['status'],'ok',rebuilt)
        self.assertEqual(VM(rebuilt['result']['bytecode']).run(),[0,0])

    # Run this suite after effect pixel encoding lands in a candidate.
    def test_effects_survive_png_and_recovered_entry_wrapper(self):
        source='''fn make(value: int) -> effect fn() = effect fn() { print(value) }
effect fn main() { let operation=make(5); operation() }
'''
        exported=self.compiler.request(dict(version=1,operation='export-png',files={'main.pxl':source}))
        self.assertEqual(exported['status'],'ok',exported)
        image=exported['result']['image']
        compiled=self.compiler.request(dict(version=1,operation='compile-png',image=image))
        self.assertEqual(compiled['status'],'ok',compiled)
        self.assertEqual(VM(compiled['result']['bytecode']).run(),[5])
        recovered=self.compiler.request(dict(version=1,operation='recover-png',image=image))
        self.assertEqual(recovered['status'],'ok',recovered)
        self.assertTrue(any('effect fn' in text for text in recovered['result']['files'].values()))
        rebuilt=self.compiler.request(dict(version=1,operation='compile',**recovered['result']))
        self.assertEqual(rebuilt['status'],'ok',rebuilt)
        self.assertEqual(VM(rebuilt['result']['bytecode']).run(),[5])
