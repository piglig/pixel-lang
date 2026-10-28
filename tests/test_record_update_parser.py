"""Native record-update syntax retains its own AST and evaluation order."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]


class RecordUpdateParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT / 'selfhost' / name).read_text()
                 for name in ('model.pxl', 'lexer.pxl', 'parser.pxl', 'source_format.pxl')}
        files['main.pxl'] = '''import "parser.pxl" as parser
import "source_format.pxl" as format
effect fn main() {
 let text = input()
 print(jsonFrom(parser.Parse("update.pxl", text)))
 print(jsonFrom(format.Format("update.pxl", text)))
}
'''
        cls.code = compile_native_sources(files)

    def parse(self, source):
        with patch('pixellang.text.parse_text', side_effect=AssertionError('host parser')):
            return json.loads(VM(self.code, input_text=source, max_steps=4000000).run()[0])

    def test_base_and_replacements_remain_ordered_ast_children(self):
        source = 'let result = previous() with { status: nextStatus(), total }'
        result = self.parse(source)
        self.assertEqual(result['diagnostics'], [])
        nodes = result['nodes']
        update, = [node for node in nodes if node['kind'] == 'record_update']
        base, first, second = [nodes[index] for index in update['children']]
        self.assertEqual(base['kind'], 'call')
        self.assertEqual((first['kind'], first['text']), ('initializer', 'status'))
        self.assertEqual(nodes[first['children'][0]]['kind'], 'call')
        self.assertEqual((second['text'], second['children']), ('total', []))
        self.assertEqual(source[update['start']:update['end']],
                         'previous() with { status: nextStatus(), total }')

    def test_updates_compose_with_fields_nested_updates_and_control_heads(self):
        for source, count in [
            ('let x = order with { status: 2 }.status', 1),
            ('let x = order with { child: order.child with { value: 3 } }', 2),
            ('let x = order with {} with { status: 2 }', 2),
            ('if order with { status: 2 } == other { print(1) }', 1),
            ('let x = order with {\n status: 2,\n total,\n}', 1),
        ]:
            with self.subTest(source=source):
                result = self.parse(source)
                self.assertEqual(result['diagnostics'], [])
                self.assertEqual(sum(n['kind'] == 'record_update' for n in result['nodes']), count)

    def test_malformed_updates_report_parse_errors(self):
        for source in ('let x = order with status',
                       'let x = order with { status: }',
                       'let x = order with { "status": 2 }',
                       'let x = order with { status: 2'):
            with self.subTest(source=source):
                result = self.parse(source)
                self.assertTrue(result['diagnostics'])
                self.assertEqual(result['diagnostics'][0]['phase'], 'parse')

    def test_formatting_preserves_update_ast_and_is_idempotent(self):
        source = 'let changed=old with{status:next(),total}\n'
        output = VM(self.code, input_text=source, max_steps=4000000).run()
        formatted = json.loads(output[1])
        self.assertEqual(formatted['diagnostics'], [])
        self.assertIn(' with {', formatted['text'])
        again = VM(self.code, input_text=formatted['text'], max_steps=4000000).run()
        self.assertEqual(json.loads(again[1]), formatted)
        before, after = json.loads(output[0]), json.loads(again[0])
        self.assertEqual([(n['kind'], n['text'], n['children']) for n in before['nodes']],
                         [(n['kind'], n['text'], n['children']) for n in after['nodes']])


if __name__ == '__main__':
    unittest.main()
