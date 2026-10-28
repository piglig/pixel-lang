"""Explicit effects survive signatures, aliases and higher-order contracts."""
import unittest
from tests import test_inout_checker as harness

class EffectCheckerTests(unittest.TestCase):
    setUpClass = classmethod(harness.InoutCheckerTests.setUpClass.__func__)
    check = harness.InoutCheckerTests.check

    def test_pure_computation_and_explicit_io(self):
        self.assertEqual(self.check('''fn twice(x: int) -> int = x * 2
effect fn main() { print(twice(2)) }
'''), [])
        diagnostics = self.check('fn main() { print(1) }')
        self.assertTrue(any(d['phase'] == 'effect' for d in diagnostics), diagnostics)

    def test_declared_effect_cannot_be_hidden_by_empty_body_or_alias(self):
        for call in ('operation()', 'let alias=operation; alias()',
                     'var alias: effect fn()=operation; alias()'):
            with self.subTest(call=call):
                diagnostics = self.check('effect fn operation() {}\nfn main() { '+call+' }')
                self.assertTrue(any(d['phase']=='effect' for d in diagnostics), diagnostics)

    def test_effect_permission_is_directional_for_function_values(self):
        self.assertEqual(self.check('''fn pure(x: int) -> int = x
effect fn main() { let allowed: effect fn(int)->int=pure; print(allowed(2)) }
'''), [])
        for value in ('impure', 'effect fn(x: int) -> int = x'):
            diagnostics = self.check('effect fn impure(x: int) -> int = x\nfn main() { let denied: fn(int)->int='+value+' }')
            self.assertTrue(diagnostics)

    def test_pure_and_effectful_higher_order_calls(self):
        prefix='''fn apply(x: int, operation: fn(int)->int) -> int = operation(x)
effect fn use(x: int, operation: effect fn(int)->int) -> int = operation(x)
effect fn external(x: int) -> int = x
'''
        self.assertEqual(self.check(prefix+'''effect fn main() {
 let value=apply(1, fn(x)=x+1)
 let next=use(value, external)
}
'''), [])
        self.assertTrue(self.check(prefix+'effect fn main() { let value=apply(1,external) }'))
        self.assertTrue(self.check('fn bad(operation: effect fn()) { operation() }'))

    def test_effectful_lambda_requires_permission_but_creation_is_pure(self):
        self.assertEqual(self.check('''fn make() -> effect fn() = effect fn() { print(1) }
effect fn main() { make()() }
'''), [])
        self.assertTrue(self.check('fn main() { let operation=fn() { print(1) } }'))
        self.assertTrue(self.check('fn main() { let operation=effect fn() {}; operation() }'))

    def test_effects_preserved_in_record_fields_and_generic_results(self):
        self.assertTrue(self.check('''record Action { run: effect fn() }
fn bad(action: Action) { action.run() }
'''))
        self.assertTrue(self.check('''fn identity[T](value: T) -> T = value
effect fn external() {}
fn main() { let action=identity(external); action() }
'''))
        self.assertEqual(self.check('''record Action { run: fn() }
fn apply(action: Action) { action.run() }
fn main() { apply(Action{run: fn() {}}) }
'''), [])

    def test_pure_flow_target_and_effectful_target_rejection(self):
        body='''flow Pipeline() step(value: int) -> int {
 output transform(value)
}
fn main() {}
'''
        self.assertEqual(self.check('fn transform(value: int) -> int = value\n'+body), [])
        self.assertTrue(self.check('effect fn transform(value: int) -> int = value\n'+body))

    def test_generic_effect_variance_and_container_invariance(self):
        self.assertEqual(self.check("""effect fn apply[T](value: T, callback: effect fn(T)->T) -> T = callback(value)
fn pure(x: int) -> int = x
effect fn main() { let x=apply(1,pure) }
"""), [])
        self.assertTrue(self.check("""fn apply[T](value: T, callback: fn(T)->T) -> T = callback(value)
effect fn external(x: int) -> int = x
effect fn main() { let x=apply(1,external) }
"""))
        self.assertTrue(self.check("""fn pure() {}
fn main() { let source: [fn()]=[pure]; let target: [effect fn()]=source }
"""))
