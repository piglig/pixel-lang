"""Expression flow grammar runs in the native PixelLang parser."""
import json
from pathlib import Path
import unittest

from pixellang.code_image import CodeImage
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "parser.pxl" as parser
record Request { source: string, editor: bool }
effect fn main() {
 match jsonDecode::[Request](input()) {
  .Ok(request)=>{Run(request)}, .Err(error)=>{print(error)}
 }
}
effect fn Run(request:Request){
 if request.editor { print(parser.ParseEditor("flow.pxl", request.source)) }
 else { print(parser.Parse("flow.pxl", request.source)) }
}'''


class NativeFlowParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT/'selfhost'/name).read_text()
                 for name in ('model.pxl', 'lexer.pxl', 'parser.pxl')}
        cls.image = CodeImage(compile_native_sources({**files, 'main.pxl': DRIVER}))

    def parse(self, source, editor=False):
        return VM(self.image, input_text=json.dumps(dict(source=source, editor=editor)),
                  max_steps=20_000_000, max_output_chars=16_000_000).run()[0]

    def test_expressions_replace_ports_without_synthesized_nodes(self):
        source = '''flow Counter() step(delta: int) -> int {
 state count = 0
 let updated: int = count + delta * 2
 next count = updated
 output updated + 1
}'''
        parsed = self.parse(source)
        self.assertEqual(parsed['diagnostics'], [])
        nodes = parsed['nodes']
        body = next(n for n in nodes if n['kind'] == 'flow_body')
        self.assertEqual([nodes[i]['kind'] for i in body['children']],
                         ['flow_state', 'flow_value', 'flow_next', 'flow_output'])
        value = next(n for n in nodes if n['kind'] == 'flow_value')
        annotation, expression = [nodes[i] for i in value['children']]
        self.assertEqual(annotation['kind'], 'annotation')
        self.assertEqual(expression['kind'], 'binary')
        self.assertEqual(expression['text'], '+')
        self.assertFalse(any(n['kind'] in ('port_ref', 'port_member', 'flow_connect', 'flow_node') for n in nodes))
        for index, node in enumerate(nodes):
            for child in node['children']:
                self.assertLess(child, index)
                self.assertLessEqual(node['start'], nodes[child]['start'])
                self.assertGreaterEqual(node['end'], nodes[child]['end'])
        self.assertEqual(source[expression['start']:expression['end']], 'count + delta * 2')

    def test_state_annotation_is_optional_and_uses_normal_annotation_shape(self):
        for declaration, kinds in [('state value = 0', ['literal_int']),
                                   ('state value: int = 0', ['annotation', 'literal_int'])]:
            with self.subTest(declaration=declaration):
                parsed = self.parse('flow F() step(x:int)->int {'+declaration+';next value=x;output value}')
                self.assertEqual(parsed['diagnostics'], [])
                state = next(n for n in parsed['nodes'] if n['kind'] == 'flow_state')
                self.assertEqual([parsed['nodes'][i]['kind'] for i in state['children']], kinds)

    def test_initial_inputs_and_persistent_child_identity_are_distinct(self):
        parsed = self.parse('''flow Parent(initial: int) step(delta: int) -> int {
 instance counter = library.Counter(initial)
 let result = counter.step(delta)
 output result
}''')
        self.assertEqual(parsed['diagnostics'], [])
        nodes = parsed['nodes']
        self.assertEqual([n['text'] for n in nodes if n['kind']=='initial_parameter'], ['initial'])
        instance = next(n for n in nodes if n['kind']=='flow_instance')
        self.assertEqual(instance['text'], 'counter')
        self.assertEqual(nodes[instance['children'][0]]['kind'], 'call')
        self.assertEqual(sum(n['kind']=='flow_instance' for n in nodes), 1)
        self.assertEqual(sum(n['kind']=='call' for n in nodes), 2)

    def test_record_updates_calls_and_matches_are_ordinary_expressions(self):
        parsed = self.parse('''flow Order(initial: Order) step(event: Event) -> Decision {
 state order: Order = initial
 let decision = transition(order, event)
 next order = order with {status: decision.status}
 output match decision.outcome {.Ok(value)=>value,.Err(error)=>fallback(error)}
}''')
        self.assertEqual(parsed['diagnostics'], [])
        nodes = parsed['nodes']
        update = next(n for n in nodes if n['kind']=='flow_next')
        self.assertEqual(nodes[update['children'][0]]['kind'], 'record_update')
        self.assertTrue(any(n['kind']=='variant_pattern' for n in nodes))

    def test_old_wiring_and_imperative_flow_statements_are_rejected(self):
        for statement in ['node n = F', 'connect x -> n.x', 'var y = x',
                          'x = 1', 'return x', 'next value.field = x']:
            with self.subTest(statement=statement):
                parsed = self.parse('flow F() step(x:int)->int {'+statement+';output x}')
                self.assertTrue(parsed['diagnostics'])
                self.assertEqual(parsed['diagnostics'][0]['phase'], 'parse')

    def test_missing_expression_and_closing_delimiter_have_source_diagnostics(self):
        for source in ['flow F() step(x:int)->int {let y = }',
                       'flow F() step(x:int)->int {next y = }',
                       'flow F() step(x:int)->int {output }',
                       'flow F() step(x:int)->int {instance child = }',
                       'flow F() step(x:int)->int {output x']:
            with self.subTest(source=source):
                result = self.parse(source)
                self.assertTrue(result['diagnostics'])
                self.assertEqual(result['diagnostics'][0]['source'], 'flow.pxl')

    def test_flow_words_remain_ordinary_local_names_in_functions(self):
        parsed = self.parse('fn copy(instance: int)->int {let step=instance;let state=step;let output=state;return output}')
        self.assertEqual(parsed['diagnostics'], [])

    def test_creation_and_step_parameter_lists_are_always_explicit(self):
        parsed = self.parse('flow Counter(value:int) step(value:int)->int {state total=value;output value}')
        self.assertEqual(parsed['diagnostics'], [])
        parameters = [(node['kind'], node['text']) for node in parsed['nodes']
                      if node['kind'] in ('initial_parameter', 'parameter')]
        self.assertEqual(parameters, [('initial_parameter', 'value'), ('parameter', 'value')])
        parsed = self.parse('flow Empty() step()->int {output 1}')
        self.assertEqual(parsed['diagnostics'], [])

    def test_unlabeled_or_missing_parameter_lists_are_rejected(self):
        for header in ('flow F(x:int)', 'flow F(initial:int)(x:int)',
                       'flow F step(x:int)', 'flow F() step'):
            with self.subTest(header=header):
                parsed = self.parse(header + '->int {output 1}')
                self.assertTrue(parsed['diagnostics'])

    def test_editor_recovers_later_function_after_invalid_flow(self):
        parsed = self.parse('flow F() step(x:int)->int {let = x}\nfn valid()->int = 1', editor=True)
        self.assertTrue(parsed['diagnostics'])
        self.assertTrue(any(n['kind']=='fn' and n['text']=='valid' for n in parsed['nodes']))


if __name__ == '__main__':
    unittest.main()
