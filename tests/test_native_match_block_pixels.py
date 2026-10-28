"""Semantic-pixel value blocks preserve structure and reject corrupt references."""
import json
import unittest

from pixellang.codec import new_source, rgba
from pixellang.vm import VM
from tests import test_native_flow_header_pixels as fixture


class NativeMatchBlockPixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.NativeFlowHeaderPixelTests.setUpClass()
        cls.code = fixture.NativeFlowHeaderPixelTests.code

    def rows(self):
        return [
            (0, [('keyword', 'fn'), ('id', 0), ('punct', '('), ('punct', ')'), ('punct', '->'), ('type', 'int')]),
            (2, [('keyword', 'return'), ('keyword', 'value_block'), ('punct', '('), ('id', 9), ('punct', ')')]),
            (0, [('keyword', 'value_body'), ('id', 9)]),
            (2, [('keyword', 'let'), ('id', 1), ('punct', ':'), ('type', 'int'), ('punct', '='), ('int', 2)]),
            (2, [('keyword', 'block_result'), ('id', 1)]),
        ]

    def parse(self, rows):
        pixels = [dict(position=[x + offset, y], rgba=rgba(kind, value))
                  for y, (offset, tokens) in enumerate(rows)
                  for x, (kind, value) in enumerate(tokens)]
        width = max(p['position'][0] for p in pixels) + 1
        links = []
        parent = None
        for y, (offset, tokens) in enumerate(rows):
            head = [offset, y]
            links.append(dict(kind='region', **{'from': head, 'to': head}))
            if offset == 0:
                parent = head
            else:
                assert parent is not None
                links.append(dict(kind='child', **{'from': parent, 'to': head}))
        document = new_source(width, len(rows), pixels, links)
        return VM(self.code, input_text=json.dumps(document), max_steps=20_000_000,
                  max_output_chars=8_000_000).run()[0]

    def test_restored_block_is_an_expression_in_the_original_function(self):
        result = self.parse(self.rows())
        self.assertEqual(result['diagnostics'], [])
        nodes = result['syntax']['nodes']
        self.assertEqual(sum(n['kind'] == 'fn' for n in nodes), 1)
        self.assertFalse(any(n['kind'] in ('lambda', 'value_ref', 'value_body') for n in nodes))
        block = next(n for n in nodes if n['kind'] == 'value_block')
        self.assertEqual([nodes[i]['kind'] for i in block['children']], ['let', 'block_result'])

    def test_missing_cyclic_and_unreferenced_bodies_are_rejected(self):
        missing = self.rows()[:2]
        cyclic = self.rows()
        cyclic[-1] = (2, [('keyword', 'block_result'), ('keyword', 'value_block'), ('punct', '('), ('id', 9), ('punct', ')')])
        unused = self.rows() + [(0, [('keyword', 'value_body'), ('id', 10)])]
        for rows, message in [(missing, 'Missing value block'), (cyclic, 'cyclic value block'), (unused, 'Unreferenced')]:
            with self.subTest(message=message):
                result = self.parse(rows)
                self.assertTrue(any(message in d['message'] for d in result['diagnostics']), result)

    def test_callable_reference_cannot_reinterpret_a_value_body(self):
        rows = self.rows()
        rows[1][1][1] = ('keyword', 'lambda')
        result = self.parse(rows)
        self.assertTrue(any('callable body' in d['message'] for d in result['diagnostics']), result)

    def test_result_requires_correct_parent_and_final_position(self):
        orphan = self.rows()
        orphan[1] = (2, [('keyword', 'block_result'), ('int', 1)])
        nonfinal = self.rows() + [(2, [('keyword', 'do'), ('int', 3)])]
        for rows, message in [(orphan, 'parent'), (nonfinal, 'final statement')]:
            with self.subTest(message=message):
                result = self.parse(rows)
                self.assertTrue(any(message in d['message'] for d in result['diagnostics']), result)


if __name__ == '__main__':
    unittest.main()
