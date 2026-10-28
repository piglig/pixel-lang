import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.native_compiler import compile_native_sources
from pixellang.codec import TABLES, rgba
from pixellang.model import PixelError
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedPixelTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name:(ROOT/'selfhost'/name).read_text() for name in ('pixel_tokens.pxl','failures.pxl')}
        files['main.pxl'] = '''import "pixel_tokens.pxl" as tokens
record Token { category: int, name: string }
record Request { tokens: [Token], ids: [int] }
effect fn main() {
 match jsonDecode::[Request](input()) {.Ok(request)=>{Run(request)},.Err(error)=>{print(error.message)}}
}
effect fn Run(request:Request) {
 let indices=tokens.Index()
 for token in request.tokens {
  match tokens.Symbol(token.category,token.name,indices) {.Ok(value)=>{print(value)},.Err(error)=>{print(error.message)}}
 }
 for id in request.ids {
  match tokens.Identifier(id) {.Ok(value)=>{print(value)},.Err(error)=>{print(error.message)}}
 }
}
'''
        cls.bytecode = compile_native_sources(files)

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
        removed = [{'category': 96, 'name': name} for name in ('mut', 'Frozen')]
        removed += [{'category': 64, 'name': name} for name in ('node', 'connect', 'try', 'catch')]
        removed += [{'category': 128, 'name': name}
                    for name in ('freeze', 'thaw', 'append', 'extend', 'pop', 'delete', 'fail')]
        for token in [{'category': 1, 'name': 'fn'}, {'category': 64, 'name': 'missing'}, *removed]:
            self.assertIn('Unknown semantic token', self.run_request([token])[0])
        for value in (-1, 65536):
            self.assertIn('Identifier must be', self.run_request([], [value])[0])

    def test_retired_error_markers_are_not_host_encodable(self):
        for kind,name in (('keyword','try'),('keyword','catch'),('builtin','fail')):
            with self.subTest(name=name), self.assertRaises(PixelError):
                rgba(kind,name)
