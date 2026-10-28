"""Contextual Option constructors preserve generic inference and independent values."""
import unittest

from pixellang.vm import VM
from tests.compiler_assertions import CompilerAssertions


class OptionInferenceTests(CompilerAssertions, unittest.TestCase):
    def test_later_evidence_and_payload_evidence(self):
        source = '''fn pick[T](value: Option[T], fallback: T) -> T = match value {Option.Some(x) => x, Option.None => fallback}
effect fn unwrap[T](value: Option[T]) {
 match value {Option.Some(x) => {print(x)}, Option.None => {assert(false)}}
}
effect fn main() {
 print(pick(Option.None, 3))
 print(pick(Option.Some(9), 0))
 unwrap(.Some(7))
 unwrap(Option.Some([1, 2]))
 match pick(Option.None, Option.Some(4)) {Option.Some(x) => {print(x)}, Option.None => {print(0)}}
}'''
        result = self.compile(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [3, 9, 7, [1, 2], 4])

    def test_inferred_payload_keeps_independent_value_semantics(self):
        source = '''fn grow[T](value: Option[T], fallback: T) -> T {
 return match value {Option.Some(x) => x, Option.None => fallback}
}
effect fn main() {
 let fallback: [int] = []
 var items = grow(Option.Some([1]), fallback)
 items.append(2)
 print(items)
 print(fallback)
}'''
        result = self.compile(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [[1, 2], []])

    def test_constructor_argument_effects_stay_in_source_order(self):
        source = '''fn pick[T](value:Option[T], fallback:T)->T = match value {Option.Some(x)=>x, Option.None=>fallback}
effect fn next(count: inout int)->int {count += 1; print(count); return count}
effect fn main() {
 var count = 0
 print(pick(Option.Some(next(&count)), next(&count)))
 print(count)
}'''
        result = self.compile(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [1, 2, 1, 2])

    def test_missing_evidence_conflicts_and_readonly_payload_writes_are_rejected(self):
        for source in [
            'fn take[T](x:Option[T]) {}\neffect fn main(){take(Option.None)}',
            'fn pick[T](x:Option[T],y:T) {}\neffect fn main(){pick(Option.Some(1),"wrong")}',
            'fn take(x:Option[[int]]) {match x {Option.Some(items)=>{items.append(2)},Option.None=>{}}}\nfn main(){}',
        ]:
            with self.subTest(source=source):
                result = self.compile(source)
                self.assertTrue(result['diagnostics'])
                self.assertTrue(any(d['phase'] == 'check' for d in result['diagnostics']), result['diagnostics'])

    def test_enum_construction_has_one_rule_for_builtin_and_user_types(self):
        source = '''enum Choice[T] { Some(value:T), None }
fn pick[T](value:Choice[T], fallback:T)->T = match value {.Some(x)=>x,.None=>fallback}
fn option[T](value:Option[T], fallback:T)->T = match value {.Some(x)=>x,.None=>fallback}
effect fn main() {
 print(pick(Choice.None, 3))
 print(pick(Choice.Some(9), 0))
 print(option(Option.None, 3))
 print(option(Option.Some(9), 0))
 print(pick(.Some(9), 0))
 print(option(.Some(9), 0))
 let chosen:Choice[int] = .Some(42)
 let present:Option[int] = .Some(42)
 print(pick(chosen,0))
 print(option(present,0))
}'''
        result = self.compile(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [3,9,3,9,9,9,42,42])

    def test_variant_names_are_ordinary_names_without_a_selector(self):
        source = '''fn Some(value:int)->int=value
record None { value:int }
effect fn main() {
 print(Some(42))
 let value = None{value:7}
 match value { Some => {print(Some.value)} }
}'''
        result = self.compile(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [42,7])
        for expression in ('Some(1)', 'None'):
            result = self.compile('fn main(){let value:Option[int]='+expression+'}')
            self.assertTrue(result['diagnostics'])

    def test_empty_variant_calls_and_patterns_are_rejected(self):
        for source in (
            'fn main(){let value=Option::[int].None()}',
            'fn main(){let value:Option[int]=.None()}',
            'enum E{Empty}\nfn main(){let value=E.Empty()}',
            'enum E{Empty}\nfn main(){match E.Empty{E.Empty()=>{}}}',
            'fn main(){let value:Option[int]=.None;match value{.None()=>{},.Some(x)=>{}}}',
        ):
            with self.subTest(source=source):
                self.assertTrue(self.compile(source)['diagnostics'])



if __name__ == '__main__':
    unittest.main()
