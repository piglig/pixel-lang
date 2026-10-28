"""Native match reachability and JSON optional-representation boundaries."""
import unittest
from pixellang.vm import VM
from tests.compiler_assertions import CompilerAssertions

class EnumBoundaryTests(CompilerAssertions, unittest.TestCase):
    def test_unreachable_wildcard_expression_and_statement(self):
        for body in [
            'print(match Option.Some(1) {Option.Some(n) => n, Option.None => 0, _ => 2})',
            'match Option.Some(1) {Option.Some(n) => {print(n)} Option.None => {} _ => {print(2)}}',
        ]:
            with self.subTest(body=body):
                result = self.compile('effect fn main(){' + body + '}')
                self.assertTrue(any('unreachable match arm' in d['message'] for d in result['diagnostics']), result['diagnostics'])

    def test_partial_and_only_wildcards_are_valid(self):
        source = 'effect fn main(){print(match Option.Some(1){Option.None=>0,_=>7}); match Option.Some(1){_=>{print(8)}}}'
        result = self.compile(source)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(VM(result['bytecode']).run(), [7,8])

    def test_adjacent_option_in_containers_and_records(self):
        for target, declaration in [
            ('Option[Option[int]]',''),
            ('[Option[Option[int]]]',''),
            ('map[string, Option[Option[int]]]',''),
            ('Box[Option[int]]','record Box[T]{value:Option[T]}'),
            ('Node','record Node{next:Option[Node], value:Option[Option[int]]}'),
        ]:
            with self.subTest(target=target):
                result=self.compile(declaration+'\neffect fn main(){let value=jsonDecode::['+target+'](input())}')
                self.assertTrue(any('adjacent Option' in d['message'] for d in result['diagnostics']), result['diagnostics'])

    def test_generic_substitution_rechecks_decode_target(self):
        source='fn decode[T](text:string)->Result[T,Error]=jsonDecode::[T](text)\neffect fn main(){let value=decode::[Option[Option[int]]]("null")}'
        result=self.compile(source)
        self.assertTrue(any('adjacent Option' in d['message'] for d in result['diagnostics']),result['diagnostics'])

    def test_separated_options_and_recursive_records_remain_valid(self):
        source='''record Node{next:Option[Node], value:int}
fn decode[T](text:string)->Result[T,Error]=jsonDecode::[T](text)
effect fn main(){
 let value=decode::[Option[[Option[int]]]]("[null,3]")
 match value {.Ok(decoded)=>{print(jsonStringify(jsonFrom(decoded)))},.Err(error)=>{print(error)}}
 let node=jsonDecode::[Node]("{\\"value\\":1,\\"next\\":{\\"value\\":2}}")
 match node {.Ok(decoded)=>{print(decoded.value)},.Err(error)=>{print(error)}}
}'''
        result=self.compile(source)
        self.assertEqual(result['diagnostics'],[])
        self.assertEqual(VM(result['bytecode']).run(),['[null,3]',1])
