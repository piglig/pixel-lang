"""Creation parameters across native checking, lowering, pixels and recovery."""
from tests.native_compiler import native_service
import unittest
from copy import deepcopy
from pathlib import Path

from pixellang.model import PixelError
from pixellang.vm import VM


FILES = {
    'child.pxl': '''export record Config { seed: int }
export flow Delay(config: Config) step(value: int) -> int {
 state previous: int = config.seed
 next previous = value
 output previous
}
''',
    'main.pxl': '''import "child.pxl" as child
flow Pair(start: int) step(value: int) -> int {
 instance first = child.Delay(child.Config{seed: start + 2})
 instance second = child.Delay(child.Config{seed: start + 10})
 let intermediate = first.step(value)
 output second.step(intermediate)
}
effect fn main() {
 let a = Pair( 3)
 let b = Pair( 30)
 print(a.step( 1))
 print(a.step( 2))
 print(b.step( 9))
 print(b.step( 10))
}
''',
}


class NativeFlowConstructorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)

    def request(self, operation, **args):
        response = self.service.request(dict(version=1, operation=operation, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def test_step_validation_is_scoped_to_each_flow_and_child_identity(self):
        flows='\n'.join(f'flow Parent{i}() step(x:int)->int {{ instance child=lib.Delay(lib.Config{{seed:{i}}}); output child.step(x) }}' for i in range(12))
        source='import "child.pxl" as lib\n'+flows+'\neffect fn main(){let a=Parent0();let b=Parent11();print(a.step(9));print(b.step(9))}'
        files={'main.pxl':source,'child.pxl':FILES['child.pxl']}
        self.assertEqual(VM(self.request('compile',files=files)['bytecode']).run(),[0,11])
        broken=source.replace('seed:7}); output child.step(x)', 'seed:7}); let first=child.step(x); output child.step(x)')
        self.assertNotEqual(source,broken)
        response=self.service.request(dict(version=1,operation='check',files={**files,'main.pxl':broken}))
        self.assertEqual(response['status'],'diagnostics',response)
        diagnostics=[d for d in response['diagnostics'] if 'more than once' in d['message']]
        self.assertEqual(len(diagnostics),1,response)
        self.assertEqual(diagnostics[0]['line'],9)

    def test_creation_configuration_is_readonly_persistent_and_captured_by_value(self):
        source = """flow Child(config: [int]) step(x: int) -> int {
 let read = fn() -> int = config[0]
 output read() + x
}
flow Parent(config: [int]) step(x: int) -> int {
 instance child = Child(config)
 state count = 0
 next count = count + config[0]
 output child.step(x) + count
}
effect fn main() {
 var input = [10]
 let handle = Parent(input)
 input[0] = 99
 print(handle.step(1))
 print(handle.step(2))
}
"""
        code = self.request('compile', files={'main.pxl': source})['bytecode']
        vm = VM(code)
        self.assertEqual(vm.run(), [11, 22])
        snapshot = vm.checkpoint()
        self.assertEqual(len(vm.flow_runtime.instances[0].configuration), 2)
        vm.restore_checkpoint(snapshot)
        image = self.request('export-png', files={'main.pxl': source})['image']
        restored = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **restored)['bytecode']).run(), [11, 22])
        for body in ('config[0] = 2', 'let config = [2]'):
            invalid = 'flow F(config:[int]) step()->int {' + body + '; output config[0]}\neffect fn main(){F([1])}'
            response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': invalid}))
            self.assertEqual(response['status'], 'diagnostics', response)

    def test_nested_instances_receive_creation_values_once(self):
        code = self.request('compile', files=FILES)['bytecode']
        vm = VM(code)
        self.assertEqual(vm.run(), [13, 5, 40, 32])
        self.assertEqual(len(vm.flow_runtime.instances), 2)
        self.assertTrue(all(len(graph['initialInputs']) == 1 for graph in code['flows'].values()))
        parent = next(graph for graph in code['flows'].values() if graph['name'] == 'Pair')
        self.assertEqual([len(node['initializers']) for node in parent['children']], [1, 1])

    def test_source_pixels_png_and_recovery_share_constructor_semantics(self):
        image = self.request('export-png', files=FILES)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), [13, 5, 40, 32])
        restored = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **restored)['bytecode']).run(), [13, 5, 40, 32])
        self.assertTrue(any(') step(' in text for text in restored['files'].values()))
        document = self.request('pixels', files=FILES)['document']
        document = deepcopy(document)
        document['metadata'] = {}
        for module in document.get('bundle', {}).get('modules', {}).values():
            module['metadata'] = {}
        self.assertEqual(VM(self.request('compile-pixels', document=document)['bytecode']).run(), [13, 5, 40, 32])

    def test_initial_data_is_a_snapshot_and_accepts_data_expressions(self):
        source = '''record Config { values: [int] }
fn Head(values: [int]) -> int = values[0]
flow Keep(initial: Config) step(unused: int) -> int {
 state items: [int] = initial.values
 let head = Head(items)
 next items = items
 output head
}
flow Indexed(initial: [int]) step() -> int {
 state value: int = initial[0] + 1
 next value = value
 output value
}
effect fn main() {
 var config: Config = Config{values: [1]}
 let first = Keep( config)
 config.values[0] = 99
 let second = Keep( config)
 print(first.step( 0))
 print(second.step( 0))
 let indexed = Indexed( [41])
 print(indexed.step())
}
'''
        self.assertEqual(VM(self.request('compile', files={'main.pxl': source})['bytecode']).run(), [1, 99, 42])

    def test_arguments_evaluate_left_to_right_once_before_creation(self):
        source = '''effect fn Evaluate(value: int) -> int { print(value); return value }
flow F(left: int, right: int) step() -> int {
 state value: int = left * 10 + right
 next value = value
 output value
}
effect fn main() { let f = F( Evaluate(4), Evaluate(2)); print(f.step()) }
'''
        self.assertEqual(VM(self.request('compile', files={'main.pxl': source})['bytecode']).run(), [4, 2, 42])

    def test_bad_constructor_signatures_scopes_and_effects_are_rejected(self):
        base = '''flow F(start: int) step(value: int) -> int {
 state total: int = start
 next total = value
 output total
}
effect fn main() { let instance = F( 1) }
'''
        cases = [
            base.replace('F( 1)', 'F()'),
            base.replace('F( 1)', 'F( true)'),
            base.replace('= start\n', '= value\n'),
            base.replace('(value: int)', '(start: int)'),
            base.replace('= start\n', '= input()\n'),
            base.replace('= start\n', '= Calculate(start)\n') + 'fn Calculate(x: int) -> int = x\n',
            base.replace('let instance = F( 1)', 'let constructor = F'),
            base.replace('effect fn main()', 'flow Outer() step(value: int) -> int { instance child = F(); output child.step(value) }\neffect fn main()'),
            base.replace('effect fn main()', 'flow Outer() step(value: int) -> int { instance child = F(value); output child.step(value) }\neffect fn main()'),
        ]
        for source in cases:
            response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
            self.assertNotEqual(response['status'], 'ok', response)
            self.assertNotIn("'phase': 'parse'", str(response))

    def test_failed_dynamic_initializer_publishes_no_instance(self):
        source = '''flow Divide(divisor: int) step() -> int {
 state value: int = 10 / divisor
 next value = value
 output value
}
effect fn main() { let invalid = Divide( 0) }
'''
        vm = VM(self.request('compile', files={'main.pxl': source})['bytecode'])
        with self.assertRaises(PixelError):
            vm.run()
        self.assertEqual(vm.flow_runtime.instances, {})
        self.assertIsNone(vm.flow_runtime.transaction)
        self.assertEqual(vm.spatial_items, 0)

    def test_projection_and_formatter_preserve_creation_boundaries(self):
        projection = self.request('native-graphs', files=FILES)['projection']
        pair = next(graph for graph in projection['graphs'] if graph['name'] == 'Pair')
        self.assertEqual([port['name'] for port in pair['initialInputs']], ['start'])
        self.assertEqual([port['name'] for port in pair['inputs']], ['value'])
        self.assertEqual([len(node['initializers']) for node in pair['children']], [1, 1])
        formatted = self.request('format', files=FILES)['text']
        files = {**FILES, 'main.pxl': formatted}
        self.assertEqual(self.request('format', files=files)['text'], formatted)
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), [13, 5, 40, 32])

    def test_constructor_contract_edit_invalidates_cached_callers(self):
        self.request('compile', files=FILES, incremental=True)
        changed = {**FILES, 'child.pxl': FILES['child.pxl'].replace('config: Config', 'config: int').replace('config.seed', 'config')}
        response = self.service.request(dict(version=1, operation='compile', files=changed, incremental=True))
        self.assertNotEqual(response['status'], 'ok', response)

    def test_real_orders_have_independent_identity_amount_and_deadline(self):
        root = Path(__file__).resolve().parents[1]
        files = {path.name: path.read_text() for path in (root / 'examples/native-orders').glob('*.pxl')}
        files.update({'std/' + path.name: path.read_text() for path in (root / 'pixellang/stdlib').glob('*.pxl')})
        expected = ['A', 1299, True, 'B', 2499, True, True]
        self.assertEqual(VM(self.request('compile', files=files, entry='instances.pxl')['bytecode']).run(), expected)
        files['deadlines.pxl'] = '''import "main.pxl" as orders
import "model.pxl" as model
import "std/time.pxl" as time
effect fn verifyDeadlines() -> Result[bool, Error] {
 let a = orders.Order( model.Order{id: "A", status: model.Status.AwaitingPayment,
                                         totalCents: 1299, expiresAt: time.Seconds(3)?})
 let b = orders.Order( model.Order{id: "B", status: model.Status.AwaitingPayment,
                                         totalCents: 2499, expiresAt: time.Seconds(10)?})
 let clock = time.At(50, time.Milliseconds(100)?)?
 print(a.step( model.Event.Tick, clock).order.status == model.Status.Expired)
 print(b.step( model.Event.Tick, clock).order.status == model.Status.AwaitingPayment)
 print(b.step( model.Event.Pay(2499), clock).order.status == model.Status.Paid)
 return .Ok(true)
}
effect fn main() {
 match verifyDeadlines() {
  .Ok(_) => {},
  .Err(error) => { print(error.message) }
 }
}
'''
        self.assertEqual(VM(self.request('compile', files=files, entry='deadlines.pxl')['bytecode']).run(), [True, True, True])


if __name__ == '__main__':
    unittest.main()
