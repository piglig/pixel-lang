import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.codec import TABLES, rgba
from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedPixelTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        files['main.pxl'] = '''import "pixel_tokens.pxl" as tokens
record Token { category: int, name: string }
record Request { tokens: [Token], ids: [int] }
fn main() {
 let request = jsonDecode[Request](input())
 for token in request.tokens { print(tokens.Symbol(token.category, token.name)) }
 for id in request.ids { print(tokens.Identifier(id)) }
}
'''
        cls.bytecode = compile_project(files)[1].bytecode

    def run_request(self, tokens, ids=None):
        vm = VM(self.bytecode, input_text=json.dumps({'tokens': tokens, 'ids': ids or []}), max_steps=10_000_000)
        with patch('pixellang.codec.rgba', side_effect=AssertionError('delegated')):
            return vm.run()

    def test_all_encoding_tables_match_current_contract(self):
        tokens, expected = [], []
        for category, (kind, names) in TABLES.items():
            for name in names:
                tokens.append({'category': category, 'name': name})
                expected.append(rgba(kind, name))
        self.assertEqual(self.run_request(tokens, [0, 255, 256, 65535]), expected + [rgba('id', i) for i in (0, 255, 256, 65535)])

    def test_invalid_symbols_and_ids(self):
        for token in ({'category': 1, 'name': 'fn'}, {'category': 64, 'name': 'missing'}):
            with self.assertRaisesRegex(PixelError, 'Unknown semantic token'):
                self.run_request([token])
        for value in (-1, 65536):
            with self.assertRaisesRegex(PixelError, 'Identifier must be'):
                self.run_request([], [value])
