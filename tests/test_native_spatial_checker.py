"""Check spatial constructors and algebra in the actual PixelLang checker."""
import json
from pathlib import Path
import unittest
import tempfile
import time

from tests.native_compiler import load_native_compiler
from pixellang.vm import VM
from pixellang.native_codegen import build_native
from pixellang.compiler_service import CompilerService

ROOT = Path(__file__).resolve().parents[1]


class NativeSpatialCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = load_native_compiler()
        cls.directory = tempfile.TemporaryDirectory(prefix='pixel-spatial-checker-')
        cls.addClassCleanup(cls.directory.cleanup)
        executable = build_native(cls.code, Path(cls.directory.name)/'checker', optimization=1, max_depth=1024)
        cls.native = CompilerService(cls.code, native_executable=executable).native

    def events(self, request):
        return self.native.execute(json.dumps(request), root=Path(self.directory.name),
            steps=30_000_000, output_chars=64_000_000, file_bytes=64_000_000,
            cancelled=lambda: False, deadline=time.monotonic()+120)

    def inspect(self, source, **modules):
        lines = self.events({'files': {'main.pxl': source, **modules}, 'operation': 'check'})
        return json.loads(lines[0]), json.loads(lines[1])

    def check(self, source, **modules):
        return self.inspect(source, **modules)[0]

    def test_function_inputs_aliases_and_captures_are_readonly(self):
        cases = [
            'fn Bad(values:[int]) { values.append(1) }',
            'fn Bad(values:[int]) { let alias=values; alias[0]=1 }',
            'fn Bad(value:Box) { value.values=[] }',
            'fn Bad(value:Box) { let alias=value.values; alias.pop() }',
            'fn Bad(values:[[int]]) { let copy=slice(values,0,len(values)); copy[0].append(1) }',
            'fn Bad(values:map[string,int]) { values.remove("missing") }',
            'fn Bad(values:[[int]]) { for value in values { value.append(1) } }',
            'fn Bad(values:[int]) { let mutate=fn(){values.append(1)}; mutate() }',
            'fn Alias(values:[int])->[int]=values\nfn Bad(values:[int]) { let same=Alias(values); same.extend([]) }',
            'fn Bad(values:[int]) { let wrapped=Option.Some(values); match wrapped {Option.Some(alias)=>{alias.append(1)},Option.None=>{}} }',
            'fn Bad(values:map[string,[int]]) { match values.get("key") {Option.Some(alias)=>{alias.append(1)},Option.None=>{}} }',
        ]
        for source in cases:
            with self.subTest(source=source):
                errors=self.check('record Box {values:[int]}\n'+source+'\nfn main(){}')
                self.assertTrue(any(e['phase']=='check' and 'readonly' in e['message'].lower() for e in errors),errors)

    def test_explicit_inout_parameters_require_writable_callers(self):
        prefix='fn Change(values:inout [int]) {values.append(1)}\n'
        self.assertEqual(self.check(prefix+'fn main(){var values=[1];Change(&values)}'),[])
        for body in ('let values=[1];Change(&values)', 'var values=[1];Change(values)',
                     'Change(&[1])'):
            with self.subTest(body=body):
                errors=self.check(prefix+'fn main(){'+body+'}')
                phase = 'parse' if body == 'Change(&[1])' else 'check'
                self.assertTrue(any(e['phase']==phase for e in errors),errors)

    def test_value_copies_and_separate_inout_calls_remain_writable(self):
        self.assertEqual(self.check("""record Box {borrowed:[int],own:[int]}
fn Identity(value:[int])->[int]=value
fn Change(value:inout [int]) {value.append(7)}
fn Good(value:[int])->int {
 let borrowed=Identity(value)
 var copy=borrowed
 Change(&copy)
 var box=Box{borrowed,own:[3]}
 box.own.append(4)
 var published=Identity(value)
 published.append(6)
 let unused=fn()->int {var local=value;local.append(9);return len(local)}
 return len(borrowed)+len(copy)+len(published)+len(box.own)
}
fn main(){}"""),[])

    def test_imported_input_mutation_is_reported_at_helper_source(self):
        errors=self.check('import "lib.pxl" as lib\nfn Bad(values:[int])->int=lib.Change(values)\nfn main(){}',
            **{'lib.pxl':'export fn Change(values:[int])->int {values.append(1);return 0}'})
        self.assertTrue(errors)
        self.assertEqual(errors[0]['source'],'lib.pxl')
        self.assertIn('readonly',errors[0]['message'].lower())

    def test_invalid_state_constants_report_source_diagnostics_without_crashing_compiler(self):
        cases = [
            ('Error', 'Error{}', 'Missing constant field'),
            ('int', '1 / 0', 'Division by zero'),
            ('int', '9223372036854775807 + 1', 'Integer overflow'),
            ('int', '-9223372036854775808 - 1', 'Integer overflow'),
            ('int', '-9223372036854775808 / -1', 'Integer overflow'),
            ('int', '-(-9223372036854775808)', 'Integer overflow'),
            ('int', '3037000500 * 3037000500', 'Integer overflow'),
            ('float64', '1.7976931348623157e308 * 2.0', 'Float64 overflow'),
            ('float64', '1.7976931348623157e308 + 1.7976931348623157e308', 'Float64 overflow'),
            ('float64', '1.7976931348623157e308 / 0.5', 'Float64 overflow'),
            ('float64', '1e309', 'finite range'),
            ('map[string, int]', 'map[string, int]{"a": 1, "a": 2}', 'Duplicate map key'),
            ('Space[World, int,string]', 'Space::[World, int,string]("")', 'Space identity'),
            ('Transform3[World, World]', 'Transform3::[World, World]{rotation: [[1.0, 0.0], [0.0, 1.0]], translation: Vec3::[World]{x: 0.0, y: 0.0, z: 0.0}}', '3 by 3'),
            ('Transform3[World, World]', 'Transform3::[World, World]{rotation: [[2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], translation: Vec3::[World]{x: 0.0, y: 0.0, z: 0.0}}', 'orthonormal'),
        ]
        for typ, value, message in cases:
            with self.subTest(value=value):
                source = f'frame World = meters\nflow Bad() step() -> {typ} {{\n state value: {typ} = {value}\n next value = value\n output value\n}}\nfn main() {{}}'
                errors = self.check(source)
                self.assertTrue(any(message in error['message'] for error in errors), errors)
                self.assertEqual(errors[0]['source'], 'main.pxl')
                self.assertEqual(errors[0]['line'], 3)

    def test_construct_and_compute_in_one_frame(self):
        self.assertEqual(self.check('''frame World = meters
effect fn main() {
 let a = Point3::[World]{x: 1.0, y: 2.0, z: 3.0}
 let b = Point3::[World]{x: 4.0, y: 6.0, z: 3.0}
 let delta: Vec3[World] = b - a
 let c: Point3[World] = a + delta
 let d: Point3[World] = delta + a
 let twice: Vec3[World] = delta * 2.0
 let same: bool = c == d
 print(twice.x)
}
'''), [])

    def test_reject_frame_mixing_invalid_algebra_and_mutation(self):
        prelude = '''frame World = meters
frame Cabin = meters
fn main() {
 var a = Point3::[World]{x: 0.0, y: 0.0, z: 0.0}
 let b = Point3::[Cabin]{x: 0.0, y: 0.0, z: 0.0}
 let delta = Vec3::[World]{x: 1.0, y: 0.0, z: 0.0}
'''
        for statement, message in [
            ('let bad = a - b', 'same coordinate frame'),
            ('let bad = a + a', 'Invalid spatial arithmetic'),
            ('let bad = delta - a', 'Invalid spatial arithmetic'),
            ('a.x = 2.0', 'immutable'),
            ('a -= a', 'Type mismatch'),
            ('let bad = Point3::[int]{x: 0.0, y: 0.0, z: 0.0}', 'declared coordinate frame'),
            ('let bad = Point3::[World]{x: 0.0, y: 0.0}', 'Missing initializer'),
            ('let bad = Point3::[World]{x: 0, y: 0.0, z: 0.0}', 'field.x'),
        ]:
            with self.subTest(statement=statement):
                errors = self.check(prelude + statement + '\n}')
                self.assertTrue(any(message in e['message'] for e in errors), errors)
                self.assertTrue(all(e['source'] == 'main.pxl' for e in errors))

    def test_flow_call_and_state_initializer_checks_are_in_the_compiler_pipeline(self):
        source="""fn Add(previous:int,delta:int)->int=previous+delta
flow Counter() step(delta:int)->int {
 state value:int=0
 let added=Add(value,delta)
 next value=added
 output added
}
fn main(){}"""
        self.assertEqual(self.check(source),[])
        self.assertEqual(self.check(source.replace('next value=added','')),[])
        for broken,phase in [
            (source.replace('value:int=0','value:int="bad"'),'check'),
            (source.replace('Add(value,delta)','Add(value,"bad")'),'check'),
            (source.replace('value:int=0','value:int=delta'),'bind'),
            (source.replace('value:int=0','value:int=parseInt(input())'),'check'),
            (source.replace('next value=added','next value=added; next value=added'),'flow'),
        ]:
            with self.subTest(source=broken):
                errors=self.check(broken)
                self.assertTrue(any(e['phase']==phase for e in errors),errors)

    def test_checked_flow_plan_retains_source_ids_and_execution_order(self):
        errors,plans=self.inspect("""fn Add(a:int,b:int)->int=a+b
flow Counter() step(delta:int)->int {
 state value:int=0
 let first=Add(value,delta)
 let second=Add(first,delta)
 next value=second
 output second
}
fn main(){}""")
        self.assertEqual(errors,[])
        self.assertEqual(len(plans),1)
        plan=plans[0];shape=plan['graph']
        first,second=shape['values']
        self.assertEqual(shape['order'],[first,second])
        self.assertTrue(any(d['source']==first and d['target']==second for d in shape['dependencies']))
        self.assertEqual(len(shape['states']),1)
        self.assertEqual(len(shape['updates']),1)
        self.assertEqual(plan['instances'],[])
        self.assertEqual(plan['diagnostics'],[])

    def test_nested_state_is_explicit_and_step_arguments_are_checked(self):
        library="""export flow Cell() step(value:int)->int {
 state previous:int=0
 next previous=value
 output previous
}
export flow Parent() step(value:int)->int {
 instance first=Cell()
 instance second=Cell()
 let intermediate=first.step(value)
 output second.step(intermediate)
}"""
        source='import "lib.pxl" as lib\neffect fn main(){let parent=lib.Parent();print(parent.step(1))}'
        errors,plans=self.inspect(source,**{'lib.pxl':library})
        self.assertEqual(errors,[])
        self.assertEqual(len(plans),2)
        self.assertTrue(all(plan['stateful'] for plan in plans))
        self.assertEqual(len(plans[1]['instances']),2)
        errors=self.check(source.replace('parent.step(1)','parent.step("bad")'),**{'lib.pxl':library})
        self.assertTrue(any(e['phase']=='check' and e['source']=='main.pxl' for e in errors),errors)

    def test_recursive_instances_are_rejected_even_without_combinational_cycles(self):
        for target in ('Recursive','Other'):
            source='flow Recursive() step(value:int)->int {instance child='+target+'();output child.step(value)}\n'
            if target=='Other':
                source+='flow Other() step(value:int)->int {instance child=Recursive();output child.step(value)}\n'
            errors=self.check(source+'fn main(){}')
            self.assertTrue(any('recursive instance dependency' in e['message'] for e in errors),errors)

    def test_stateless_nested_flows_use_explicit_instances(self):
        errors,plans=self.inspect("""fn Identity(value:int)->int=value
flow Child() step(value:int)->int {output Identity(value)}
flow Parent() step(value:int)->int {
 instance first=Child()
 instance second=Child()
 let intermediate=first.step(value)
 output second.step(intermediate)
}
effect fn main(){let parent=Parent();print(parent.step(1))}""")
        self.assertEqual(errors,[])
        self.assertEqual(len(plans),2)
        self.assertEqual([plan['stateful'] for plan in plans],[False,True])
        self.assertEqual(len(plans[1]['instances']),2)

    def test_function_effects_follow_imports_aliases_and_recursive_helpers(self):
        library = '''export effect fn read() -> Result[string,Error] = readText("data.txt")'''
        errors = self.check('''import "lib.pxl" as lib
fn Read() -> Result[string,Error] { let reader = lib.read; return reader() }
fn main() {}
''', **{'lib.pxl': library})
        self.assertTrue(any(e['phase'] == 'effect' and 'call' in e['message'].lower() for e in errors), errors)
        self.assertTrue(all(e['source'] == 'main.pxl' for e in errors), errors)
        errors = self.check('''fn first(value: int) -> int = second(value)
fn second(value: int) -> int { if value == 0 { print(value) }; return first(value - 1) }
fn Run(value: int) -> int = first(value)
fn main() {}
''')
        self.assertTrue(any(e['phase'] == 'effect' and 'print' in e['message'] for e in errors), errors)

    def test_function_effects_distinguish_called_and_uncalled_closures(self):
        source = '''fn Run(value: int) -> int {
 let unused = effect fn() { print(value) }
 let pure = fn(x: int) -> int = x + 1
 return pure(value)
}
effect fn main() { print(Run(3)) }
'''
        self.assertEqual(self.check(source), [])
        errors = self.check(source.replace('return pure(value)', 'unused(); return pure(value)'))
        self.assertTrue(any(e['phase'] == 'effect' and 'effect fn' in e['message'] for e in errors), errors)

    def test_function_pure_helpers_local_mutation_and_constructors_remain_legal(self):
        self.assertEqual(self.check('''record Box { value: int }
enum Choice { Value(value: int), Empty }
fn count(value: int) -> int {
 if value <= 0 { return 0 }
 return count(value - 1) + 1
}
fn Run(value: int) -> Choice {
 var local: [int] = []
 local.append(count(value))
 let box = Box{value: local[0]}
 return Choice.Value(box.value)
}
effect fn main() { print(Run(3)) }
'''), [])

    def test_function_effect_boundaries_allow_pure_callbacks_and_effectful_space_creation(self):
        for source, effect in [
            ('fn Run()->string=input()', 'input'),
            ('fn Run()->[string]=programArgs()', 'programArgs'),
            ('fn Run()->Result[string,Error]=readText("file")', 'readText'),
            ('frame World=meters\nfn Run()->Space[World,int,string]=Space::[World,int,string]("world")', 'space-new'),
        ]:
            with self.subTest(effect=effect):
                errors=self.check(source+'\nfn main(){}')
                self.assertTrue(any(e['phase']=='effect' and effect in e['message'] for e in errors),errors)
        for source in (
            'fn Run(f:fn(int)->int,value:int)->int=f(value)',
            'fn Pure(value:int)->int=value\nfn Run(value:int)->int {var callback=Pure;return callback(value)}',
            'frame World=meters\neffect fn Run()->Space[World,int,string]=Space::[World,int,string]("world")',
        ):
            with self.subTest(source=source):
                self.assertEqual(self.check(source+'\nfn main(){}'),[])

    def test_effect_dependencies_participate_in_incremental_interfaces(self):
        def interface(source):
            lines = self.events({'files': {'main.pxl': source}, 'operation': 'interfaces'})
            return json.loads(lines[0])['interface']
        borrowed = interface('fn alias(values: [int]) -> [int] = values')
        self.assertNotEqual(borrowed, interface('fn alias(values: [int]) -> [int] = slice(values, 0, len(values))'))
        read = interface('fn size(values: [int]) -> int = len(values)')
        self.assertNotEqual(read, interface('fn size(values: inout [int]) -> int { values.append(1); return len(values) }'))
        self.assertEqual(interface('fn grow(values: inout [int]) -> int { values.append(1); return 1 }'),
                         interface('fn grow(values: inout [int]) -> int { values.append(2); return 2 }'))
        first = interface('fn value() -> int = 1')
        self.assertEqual(first, interface('fn value() -> int = 2'))
        self.assertEqual(first, interface('fn value() -> int { let extra = [1, 2, 3]; return len(extra) }'))
        self.assertNotEqual(first, interface('fn value() -> int { print(1); return 1 }'))
        self.assertNotEqual(interface('fn value() -> int = helper()'), interface('fn value() -> int = other()'))
        self.assertNotEqual(interface('fn value() -> int { let f = helper; return f() }'),
                            interface('fn value() -> int { let f = other; return f() }'))

    def test_native_checker_matches_reference(self):
        for source in ('flow F() step(x:int)->int {output x}\nfn main(){}',
                       'fn Bad(values:[int]){values.append(1)}\nfn main(){}'):
            request={'files':{'main.pxl':source},'operation':'check'}
            expected=VM(self.code,input_text=json.dumps(request),max_steps=30_000_000).run()
            self.assertEqual([json.loads(event) for event in self.events(request)],
                             [json.loads(event) for event in expected])
