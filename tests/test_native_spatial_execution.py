"""Native source -> self-hosted checker/IR/assembler -> VM spatial values."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from pixellang.bootstrap import read_bytecode_stream
from pixellang.model import PixelError
from tests.native_compiler import load_native_compiler
from pixellang.vm import VM, validate_bytecode

ROOT = Path(__file__).resolve().parents[1]


class NativeSpatialExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = load_native_compiler(driver=None)

    def native_events(self, source, operation, **modules):
        request = {'source': 'main.pxl', 'text': source,
                   'files': {'main.pxl': source, **modules}, 'operation': operation}
        return VM(self.compiler, input_text=json.dumps(request),
                  max_steps=50_000_000, max_heap_items=16_000_000).run()

    def compile_events(self, source, **modules):
        # Check both independent compiler transports; do not synthesize a stream.
        return (self.native_events(source, 'bytecode', **modules)
                + self.native_events(source, 'bytecode-stream', **modules))

    def compile_result(self, source, **modules):
        return json.loads(self.native_events(source, 'bytecode', **modules)[0])

    def debug_result(self, source):
        output = self.native_events(source, 'studio-bytecode-stream')
        debug = output[-1] if isinstance(output[-1], dict) else json.loads(output[-1])
        return read_bytecode_stream(output[:-1]), debug

    def compile(self, source, **modules):
        output = self.compile_result(source, **modules)
        self.assertEqual(output['diagnostics'], [])
        return output['bytecode']

    def test_function_inputs_and_published_outputs_have_value_boundaries(self):
        code = self.compile('''record Box { values: [int] }
fn Identity(value: Box) -> Box = value
fn Fresh(value: Box) -> [int] {
 var copy = value.values
 copy.append(7)
 return copy
}
effect fn main() {
 var original: Box = Box{values: [1]}
 let published = Identity(original)
 original.values.append(2)
 print(published.values)
 original.values.append(3)
 print(Fresh(original))
 print(original.values)
}
''')
        self.assertEqual(VM(code).run(), [[1], [1, 2, 3, 7], [1, 2, 3]])

    def test_function_sorts_owned_copy_through_mutating_helper(self):
        code = self.compile('''fn swap(values: inout [int], a: int, b: int) {
 let temporary = values[a]
 values[a] = values[b]
 values[b] = temporary
}
fn Sorted(values: [int]) -> [int] {
 var result = values
 var i = 0
 while i < len(result) {
  var j = i + 1
  while j < len(result) {
   if result[j] < result[i] { swap(&result, i, j) }
   j += 1
  }
  i += 1
 }
 return result
}
effect fn main() {
 let inputValues = [4, 1, 3, 1]
 var sorted = Sorted(inputValues)
 print(sorted)
 print(inputValues)
 sorted.append(9)
 print(inputValues)
}
''')
        self.assertEqual(VM(code).run(), [[1, 1, 3, 4], [4, 1, 3, 1], [4, 1, 3, 1]])

    def test_debug_metadata_covers_native_state_initializers_and_coordinate_frames(self):
        source = '''frame World = meters
flow Counter() step(delta: int) -> int {
 state previous: int = 1 + 2
 next previous = delta
 output previous
}
effect fn main() { let handle = Counter(); print(handle.step( 7)) }
'''
        compiled, debug = self.debug_result(source)
        self.assertEqual(compiled['diagnostics'], [])
        code = compiled['bytecode']
        self.assertEqual(set(code['functions']), set(debug['functions']))
        graph_id, graph = next(iter(code['flows'].items()))
        self.assertEqual(debug['functions'][graph_id]['names'], ['delta', 'previous'])
        initial = graph['states'][0]['initializer']
        self.assertEqual(debug['functions'][initial]['name'], 'Counter.initial.previous')
        self.assertEqual(debug['functions'][initial]['names'], [])
        self.assertIn('main.pxl:World', debug['types'].values())
        self.assertEqual(VM(code).run(), [3])

    def test_native_values_survive_calls_arrays_arithmetic_and_debug(self):
        code = self.compile(r'''frame World = meters
fn advance(p: Point3[World], v: Vec3[World]) -> Point3[World] = p + v
effect fn main() {
 let p = Point3::[World]{z: 3.0, x: 1.0, y: 2.0}
 let v = Vec3::[World]{x: 2.0, y: 0.0, z: 4.0}
 let result = advance(p, v)
 var delta = result - p
 delta *= 2.0
 let values = [p, result]
 print(values[1].z)
 print(delta)
 print(p == values[0])
 print(jsonFrom(p))
 let decoded = jsonDecode::[Point3[World]]("{\"x\":1.0,\"y\":2.0,\"z\":3.0}")
 match decoded { .Ok(value) => { print(value == p) }, .Err(error) => { print(error.message) } }
}
''')
        output = VM(code).run()
        self.assertEqual(output[:3], [7.0, {'x': 4.0, 'y': 0.0, 'z': 8.0}, True])
        self.assertEqual(json.loads(output[3]), {'x': 1.0, 'y': 2.0, 'z': 3.0})
        self.assertTrue(output[4])
        self.assertEqual(list(code['coordinateFrames'].values()), ['meters'])
        instructions = [i for f in code['functions'].values() for i in f['instructions']]
        self.assertTrue(any(i['op'] == 'SPATIAL_VALUE' for i in instructions))
        self.assertTrue(any(i['op'] == 'SPATIAL_BINARY' for i in instructions))
        self.assertTrue(all(i['span']['source'] == 'main.pxl' for i in instructions))
        broken = deepcopy(code)
        broken['coordinateFrames'] = {}
        with self.assertRaises(PixelError): validate_bytecode(broken)

    def test_invalid_coordinate_aborts_without_mutating_prior_value(self):
        code = self.compile('''frame World = meters
effect fn main() {
 let v = Vec3::[World]{x: 1e308, y: 0.0, z: 0.0}
 print(v.x)
 print(v * 2.0)
 print("unreachable")
}
''')
        vm = VM(code)
        with self.assertRaisesRegex(PixelError, 'finite') as caught:
            vm.run()
        self.assertEqual(caught.exception.span['source'], 'main.pxl')
        self.assertEqual(vm.output, [1e308])

    def test_entities_neighbors_direction_and_value_payloads(self):
        code = self.compile(r'''frame World = meters
record Data { values: [int] }
effect fn main() {
 let origin = Point3::[World]{x: 0.0, y: 0.0, z: 0.0}
 let near = Point3::[World]{x: 0.0, y: 0.0, z: 2.0}
 let far = Point3::[World]{x: 0.0, y: 0.0, z: 3.0}
 let forward = Vec3::[World]{x: 0.0, y: 0.0, z: 1.0}
 var data: Data = Data{values: [10, 20]}
 let empty = Space::[World, Data,string]("room")
 let first = empty.insert("entity-0", origin, data)
 let before = first.insert("entity-1", near, data)
 let a = before.require(before.entityId("entity-0"))
 let b = before.require(before.entityId("entity-1"))
 data.values[0] = 99
 var retrieved = a.payload
 print(retrieved.values[0])
 retrieved.values[0] = 55
 print(a.payload.values[0])
 print(len(before.neighbors(a.id, 2.0)))
 let after = before.move(b.id, far)
 print(len(after.neighbors(a.id, 2.0)))
 print(before.require(before.entityId("entity-1")).position.z)
 print(after.require(after.entityId("entity-1")).position.z)
 print(after.ahead(a.id, b.id, forward, 0.0))
 print(origin.distance(near))
 print(forward.dot(forward))
 let removed = after.remove(b.id)
 let inserted = removed.insert("entity-2", near, data)
 print(inserted.require(inserted.entityId("entity-2")).id.key)
 print(empty.size())
 let other = Space::[World, Data,string]("other").insert("entity-3", origin, data)
 print(other.size())
}
''')
        self.assertEqual(VM(code).run(), [10, 10, 1, 0, 2.0, 3.0, True, 2.0, 1.0, "entity-2", 0, 1])

    def test_explicit_transform_applies_units_and_copies_rotation(self):
        code = self.compile(r'''frame Cabin = centimeters
frame World = meters
frame Target = millimeters
effect fn main() {
 var rotation: [[float64]] = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
 let transform = Transform3::[Cabin, World]{rotation: rotation, translation: Vec3::[World]{x: 10.0, y: 0.0, z: 0.0}}
 rotation[0][0] = 9.0
 var copied = transform.rotation
 copied[0][0] = 8.0
 let p = Point3::[Cabin]{x: 100.0, y: 0.0, z: 0.0}
 let v = Vec3::[Cabin]{x: 100.0, y: 0.0, z: 0.0}
 print(transform.point(p).x)
 print(transform.vector(v).x)
 let next = Transform3::[World, Target]{rotation: [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]], translation: Vec3::[Target]{x: 0.0, y: 0.0, z: 20.0}}
 let combined = transform.then(next)
 print(combined.point(p) == next.point(transform.point(p)))

}
''')
        self.assertEqual(VM(code).run(), [11.0, 1.0, True])

    def test_invalid_space_identity_and_rotation_are_execution_faults(self):
        prefix = '''frame World = meters
effect fn main(){
 let origin=Point3::[World]{x:0.0,y:0.0,z:0.0}
 let first=Space::[World,int,string]("first").insert("entity-0", origin,1)
 let other=Space::[World,int,string]("other").insert("entity-1", origin,2)
'''
        for statement, message in (
            ('print(other.neighbors(first.entityId("entity-0"),2.0))', 'belonging to this space'),
            ('let bad=Transform3::[World,World]{rotation:[[9.0,0.0,0.0],[0.0,1.0,0.0],[0.0,0.0,1.0]],translation:Vec3::[World]{x:0.0,y:0.0,z:0.0}}', 'orthonormal'),
        ):
            with self.subTest(statement=statement):
                code=self.compile(prefix+statement+';print("unreachable")}')
                vm=VM(code)
                with self.assertRaisesRegex(PixelError,message) as caught:
                    vm.run()
                self.assertEqual(caught.exception.span['source'],'main.pxl')
                self.assertEqual(vm.output,[])

    def test_space_and_transform_type_errors_are_compile_time_diagnostics(self):
        prefix = '''frame World = meters
frame Other = meters
effect fn main() {
 let p = Point3::[World]{x: 0.0, y: 0.0, z: 0.0}
 let wrong = Point3::[Other]{x: 0.0, y: 0.0, z: 0.0}
 let space = Space::[World, int,string]("a")
'''
        for statement in [
            'let bad = space.insert("sample", wrong, 1)',
            'let bad = space.insert("sample", p, "wrong payload")',
            'let bad = space.neighbors(space.entityId("sample"), true)',
            'let bad = Space::[int, int,string]("b")',
            'let bad = p.distance(wrong)',
            'let bad = Transform3::[World, Other]{rotation: [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], translation: Vec3::[World]{x: 0.0, y: 0.0, z: 0.0}}',
        ]:
            with self.subTest(statement=statement):
                output = self.compile_result(prefix + statement + '\n}')
                self.assertTrue(output['diagnostics'])
                self.assertFalse(output['bytecode']['entry'])
                self.assertEqual(output['diagnostics'][0]['source'], 'main.pxl')

    def test_functions_are_native_callable_declarations_and_retain_identity(self):
        code = self.compile(r'''frame World = meters
fn Double(values: [int]) -> [int] {
 var result: [int] = []
 for value in values { result.append(value * 2) }
 return result
}
fn Total(values: [int]) -> int {
 var total = 0
 for value in values { total += value }
 return total
}
fn Advance(p: Point3[World], delta: Vec3[World]) -> Point3[World] = p + delta
fn Identity[T](value: T) -> T = value
effect fn main() {
 print(Total(Double([1, 2, 3])))
 let identity = Identity::[int]
 print(identity(7))
 let result = Advance(Point3::[World]{x: 0.0, y: 0.0, z: 2.0}, Vec3::[World]{x: 0.0, y: 0.0, z: 3.0})
 print(result.z)
}
''')
        self.assertEqual(VM(code).run(), [12, 7, 5.0])
        self.assertNotIn('componentFunctions', code)
        self.assertEqual({instruction['span']['source'] for function in code['functions'].values()
                          for instruction in function['instructions']}, {'main.pxl'})

    def test_native_nested_flow_executes_expressions_and_field_projections(self):
        example = ROOT / 'examples' / 'native-pipeline'
        library = (example / 'lib.pxl').read_text()
        source = (example / 'main.pxl').read_text()
        events = self.compile_events(source, **{'lib.pxl': library})
        assembled = json.loads(events[0])
        self.assertEqual(assembled['diagnostics'], [])
        streamed = read_bytecode_stream(events[1:])
        self.assertEqual(streamed, assembled)
        code = assembled['bytecode']
        self.assertEqual(VM(code).run(), [24, 16])
        self.assertEqual(len(code['flows']), 2)
        pipeline = next(g for g in code['flows'].values() if g['name'] == 'Pipeline')
        total = next(value for value in pipeline['values'] if value['name'] == 'total')
        double = next(value for value in pipeline['values'] if value['name'] == 'doubled')
        self.assertEqual(pipeline['order'], [double['node'], total['node']])
        self.assertTrue(any(e['source'] == double['node'] and e['target'] == total['node'] for e in pipeline['dependencies']))
        combined = next(g for g in code['flows'].values() if g['name'] == 'Combined')
        self.assertEqual([child['name'] for child in combined['children']], ['left', 'right'])
        self.assertNotEqual(combined['values'][0]['slot'], combined['values'][1]['slot'])
        sources = {i['span']['source'] for f in code['functions'].values() for i in f['instructions']}
        self.assertEqual(sources, {'main.pxl', 'lib.pxl'})

    def test_native_flow_spatial_expressions_and_input_passthrough(self):
        source = """frame World = meters
fn Advance(p: Point3[World], v: Vec3[World]) -> Point3[World] = p + v
flow Height() step(p: Point3[World], v: Vec3[World]) -> float64 {
 let move = Advance(p,v)
 output move.z
}
flow Identity() step(value: int) -> int { output value }
effect fn main() {
 print(Height().step(Point3::[World]{x: 0.0, y: 0.0, z: 2.0}, Vec3::[World]{x: 0.0, y: 0.0, z: 3.0}))
 print(Identity().step(7))
}
"""
        code = self.compile(source)
        self.assertEqual(VM(code).run(), [5.0, 7])
        height = next(g for g in code['flows'].values() if g['name'] == 'Height')
        self.assertEqual(height['output']['type'], 'float64')
        identity = next(g for g in code['flows'].values() if g['name'] == 'Identity')
        self.assertEqual(identity['values'], [])
        self.assertTrue(any(e['source'] == identity['inputs'][0]['node'] and e['target'] == identity['returnNode'] for e in identity['dependencies']))

    def test_unused_flow_values_execute_and_fail_at_original_source(self):
        source = """fn Divide(value: int) -> int = 1 / value
flow Evaluate() step(value: int) -> int {
 let unused = Divide(value)
 output value
}
effect fn main() {
 print(Evaluate().step(2))
}
"""
        code = self.compile(source)
        self.assertEqual(VM(code).run(), [2])
        failed = self.compile(source.replace('print(Evaluate().step(2))', 'print(Evaluate().step(0))'))
        with self.assertRaises(PixelError) as caught:
            VM(failed).run()
        self.assertEqual(caught.exception.span['source'], 'main.pxl')

    def test_flow_metadata_must_match_executable_and_stream_counts(self):
        events = self.compile_events("""fn Pass(value: int) -> int = value
flow Flow() step(value: int) -> int {
 let passed = Pass(value)
 output passed
}
effect fn main() { print(Flow().step(4)) }
""")
        code = json.loads(events[0])['bytecode']
        flow_id = next(iter(code['flows']))
        for mutation in ['order', 'type', 'slot', 'instructions', 'target', 'missing', 'duplicate']:
            broken = deepcopy(code)
            graph = broken['flows'][flow_id]
            if mutation == 'order': graph['order'] = []
            elif mutation == 'type': graph['output']['type'] = 'str'
            elif mutation == 'slot': graph['values'][0]['slot'] += 1
            elif mutation == 'instructions':
                next(i for i in broken['functions'][flow_id]['instructions'] if i['op'] == 'STORE')['node'] = 999999
            elif mutation == 'target': graph['dependencies'][0]['target'] = 999999
            elif mutation == 'missing': graph['values'] = []
            else: graph['values'].append(deepcopy(graph['values'][0]))
            with self.subTest(mutation=mutation), self.assertRaises(PixelError):
                validate_bytecode(broken)
        bad_events = events[1:]
        end = json.loads(bad_events[-1])
        end['flows'] = 0
        bad_events[-1] = json.dumps(end)
        with self.assertRaises(ValueError): read_bytecode_stream(bad_events)

    def test_native_stateful_instances_have_typed_independent_state(self):
        code = self.compile((ROOT / 'examples' / 'native-counter' / 'main.pxl').read_text())
        vm = VM(code)
        self.assertEqual(vm.run(), [1, 3, 10, 6])
        instances = list(vm.flow_runtime.instances.values())
        self.assertEqual([instance.steps for instance in instances], [3, 1])
        self.assertEqual([list(instance.cells.values()) for instance in instances], [[6], [10]])
        graph = next(iter(code['flows'].values()))
        self.assertTrue(graph['stateful'])
        self.assertEqual(len(graph['states']), 1)
        initializer = code['functions'][graph['states'][0]['initializer']]
        self.assertEqual(initializer['params'], [])
        self.assertEqual(initializer['result'], 'int')

    def test_nested_state_updates_commit_together_and_rollback_on_failure(self):
        code = self.compile('''fn Add(previous: int, delta: int) -> int = previous + delta
fn Divide(value: int, divisor: int) -> int = value / divisor
fn One(value: int) -> int = 1
flow Counter() step(delta: int, divisor: int) -> int {
 state value: int = 0
 let added = Add(value,delta)
 let divided = Divide(added,divisor)
 next value = added
 output divided
}
flow Parent() step(delta: int, divisor: int) -> int {
 instance first = Counter()
 instance second = Counter()
 let one = One(delta)
 let firstValue = first.step(delta,one)
 output second.step(firstValue,divisor)
}
effect fn main() {
 let parent = Parent()
 print(parent.step( 2, 1))
 print(parent.step( 3, 0))
}
''')
        vm = VM(code)
        before = vm.checkpoint()
        with self.assertRaises(PixelError): vm.run()
        self.assertEqual(vm.output, [2])
        final = vm.checkpoint()
        instance = next(iter(vm.flow_runtime.instances.values()))
        self.assertEqual(instance.steps, 1)
        self.assertEqual(sorted(instance.cells.values()), [2, 2])
        self.assertEqual(len({path for path, cell in instance.cells}), 2)
        self.assertIsNone(vm.flow_runtime.transaction)
        vm.restore_checkpoint(before)
        with self.assertRaises(PixelError): vm.run()
        self.assertEqual(vm.output, [2])
        self.assertEqual(vm.checkpoint(), final)

    def test_native_state_snapshots_do_not_alias_returned_arrays(self):
        code = self.compile('''fn Add(previous: [int], value: int) -> [int] {
 var result = previous
 result.append(value)
 return result
}
flow History() step(value: int) -> [int] {
 state values: [int] = []
 let added = Add(values,value)
 next values = added
 output added
}
effect fn main() {
 let history = History()
 var first = history.step( 1)
 first.append(99)
 print(first)
 print(history.step( 2))
}
''')
        self.assertEqual(VM(code).run(), [[1, 99], [1, 2]])

    def test_native_space_state_is_namespaced_per_instance_and_replayable(self):
        code = self.compile('''frame World = meters
fn Insert(world: Space[World, int,string], value: int) -> Space[World, int,string] = world.insert(string(value), Point3::[World]{x: 0.0, y: 0.0, z: 0.0}, value)
fn Size(world: Space[World, int,string]) -> int = world.size()
flow Scene() step(value: int) -> int {
 state world: Space[World, int,string] = Space::[World, int,string]("world")
 let inserted = Insert(world,value)
 let size = Size(inserted)
 next world = inserted
 output size
}
effect fn main() {
 let first = Scene()
 let second = Scene()
 print(first.step( 1))
 print(first.step( 2))
 print(second.step( 3))
}
''')
        vm = VM(code)
        # Checkpoint while initialization or a step is active, not just at main.
        while vm.flow_runtime.transaction is None: vm.step(snapshot=False)
        checkpoint = vm.checkpoint()
        self.assertEqual(vm.run(), [1, 2, 1])
        final = vm.checkpoint()
        instances = list(vm.flow_runtime.instances.values())
        worlds = [next(iter(instance.cells.values())) for instance in instances]
        self.assertNotEqual(worlds[0].identity, worlds[1].identity)
        self.assertEqual([len(world.entities) for world in worlds], [2, 1])
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(), [1, 2, 1])
        self.assertEqual(vm.checkpoint(), final)

    def test_invalid_initializers_and_effects_are_compile_errors(self):
        result = self.compile_result('''flow Broken() step(value: int) -> int {
 state cell: int = 1 / 0
 next cell = value
 output cell
}
effect fn main() { let broken = Broken() }
''')
        self.assertTrue(any('Division by zero' in error['message'] for error in result['diagnostics']))
        result = self.compile_result('fn Effect(value: int) -> int { print(value); return value }\neffect fn main() {}')
        self.assertTrue(any(error['phase'] == 'effect' for error in result['diagnostics']))

    def test_flow_initializer_arithmetic_is_folded_with_runtime_equivalent_values(self):
        integers = ['1 + 2 * 3', '-7 / 3', '-7 % 3', '-9223372036854775808 % -1',
                    '9223372036854775807 - 1', '-9223372036854775808 + 1',
                    '-9223372036854775808 * 1', '-3037000499 * -3037000499']
        floats = ['1.7976931348623157e308 * 1.0', '(1.7976931348623157e308 / 2.0) * 2.0',
                  '1.7976931348623157e308 / 2.0 + 1.7976931348623157e308 / 2.0',
                  '-1.7976931348623157e308 / 2.0 * 2.0', '1e308 % 3.0', '1e-308 * 1e-10',
                  '-0.0 * 3.0', '-0.0 / 3.0', '-0.0 + 0.0', '1e-320 / 2.0', '5e-324 * 0.5']
        for typ, expressions in [('int', integers), ('float64', floats)]:
            values = '[' + ', '.join(expressions) + ']'
            source = f'flow Values() step() -> [{typ}] {{ state values: [{typ}] = {values}; next values = values; output values }}\n'
            source += f'effect fn main() {{ print(Values().step()); print({values}) }}'
            code = self.compile(source)
            output = VM(code).run()
            self.assertEqual(output[0], output[1])
            if typ == 'float64':
                self.assertEqual([value.hex() for value in output[0]], [value.hex() for value in output[1]])
            initializer = next(iter(code['flows'].values()))['states'][0]['initializer']
            instructions = code['functions'][initializer]['instructions']
            self.assertEqual([ins['op'] for ins in instructions], ['PUSH'] * len(expressions) + ['ARRAY', 'RETURN'])

    def test_composite_spatial_constants_and_short_circuit_are_materialized(self):
        code = self.compile('''frame World = meters
record Data { value: int, labels: map[string, int] }
flow Values() step() -> Vec3[World] {
 state data: Data = Data{value: 2 * 3, labels: map[string, int]{"sum": 3 + 4}}
 state selected: int = Data{value: 7 + 8, labels: map[string, int]{}}.value
 state flag: bool = false && 1 / 0 == 1
 state choice: Option[int] = Option.Some(3 + 4)
 state position: Vec3[World] = (Point3::[World]{x: 5.0, y: 3.0, z: 2.0} - Point3::[World]{x: 1.0, y: 1.0, z: 1.0}) * 2.0
 next data = data
 next selected = selected
 next flag = flag
 next choice = choice
 next position = position
 output position
}
effect fn main() { print((Values()).step()) }
''')
        vm = VM(code)
        self.assertEqual(vm.run(), [{'x': 8.0, 'y': 4.0, 'z': 2.0}])
        instance = next(iter(vm.flow_runtime.instances.values()))
        self.assertIn(15, instance.cells.values())
        self.assertIn(False, instance.cells.values())
        for graph in code['flows'].values():
            for cell in graph['states']:
                instructions = code['functions'][cell['initializer']]['instructions']
                self.assertFalse(any(ins['op'] in {'ADD', 'SUB', 'MUL', 'DIV', 'MOD', 'FIELD', 'JUMP_IF_FALSE', 'SPATIAL_BINARY'} for ins in instructions))

    def test_flow_instance_signatures_reject_incorrect_source_usage(self):
        source = '''flow Counter() step(value: int) -> int {
 state cell: int = 0
 next cell = value
 output cell
}
effect fn main() { BODY }
'''
        for body in ['let instance = 1()',
                     'let instance = Counter(); instance.step( "wrong")',
                     'let instance = Counter(); instance.step()',
                     '(1).step( 2)', 'Counter(1)',
                     'let instance: Flow[int] = Counter()']:
            with self.subTest(body=body):
                result = self.compile_result(source.replace('BODY', body))
                self.assertTrue(result['diagnostics'])
