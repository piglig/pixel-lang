from tests.native_compiler import native_driver
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.bootstrap import compile_with
from pixellang.text import Source
from tests.reference_lexer import lex
from pixellang.vm import VM
from pixellang.code_image import CodeImage
from tests.selfhost_runtime import SELFHOST_MAX_OUTPUT_CHARS

ROOT = Path(__file__).resolve().parents[1] / "selfhost"


class SelfHostedLexerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in ROOT.glob("*.pxl")}
        from tests.native_compiler import load_native_compiler
        cls.bytecode = load_native_compiler(driver=None)
        cls.image = CodeImage(cls.bytecode)

    def scan(self, text, source="test.pxl"):
        vm = VM(
            self.image,
            input_text=json.dumps({"source": source, "text": text}),
            max_steps=20_000_000,
            max_output_chars=SELFHOST_MAX_OUTPUT_CHARS,
        )
        # Running the PixelLang lexer may not delegate to either Python frontend.
        with (
            patch(
                "pixellang.text.parse_text",
                side_effect=AssertionError("frontend delegated"),
            ),
            patch("pixellang.text.lex", side_effect=AssertionError("lexer delegated")),
        ):
            return vm.run()[0]

    def test_independent_token_values_and_locations(self):
        text = 'let 数量 = 1_234\n/* a\nb */数量 += 2.5e-1; "\\u0041"'
        result = self.scan(text)
        self.assertEqual(result["diagnostics"], [])
        tokens = result["tokens"]
        self.assertEqual(
            [t["value"] for t in tokens],
            [
                "let",
                "数量",
                "=",
                "1234",
                "\n",
                "\n",
                "数量",
                "+=",
                "2.5e-1",
                ";",
                "A",
                "<eof>",
            ],
        )
        self.assertEqual(
            (
                tokens[1]["start"],
                tokens[1]["end"],
                tokens[1]["line"],
                tokens[1]["column"],
            ),
            (4, 6, 1, 5),
        )
        self.assertEqual(tokens[6]["line"], 3)
        for token in tokens:
            self.assertEqual(text[token["start"] : token["end"]], token["text"])

    def test_own_sources_match_independent_lexemes(self):
        with native_driver(self.image) as run:
            for name, text in self.files.items():
                with self.subTest(source=name):
                    expected = lex(Source(text, name))
                    result = run(dict(source=name,text=text),steps=20_000_000,output_chars=SELFHOST_MAX_OUTPUT_CHARS)[0]
                    self.assertEqual(result["diagnostics"], [])
                    self.assertEqual(len(result["tokens"]), len(expected))
                    for actual, token in zip(result["tokens"], expected):
                        kind = "name" if actual["kind"] == "keyword" else actual["kind"]
                        self.assertEqual(
                            (kind, actual["start"], actual["end"]),
                            (token.kind, token.start, token.end),
                        )
                        value = actual["value"]
                        if kind == "int":
                            value = int(value)
                        elif kind == "float64":
                            value = float(value)
                        self.assertEqual(value, token.value)

    def test_errors_have_exact_source_location(self):
        for text, start, line, column in [
            ("fn main() {\n @ }", 13, 2, 2),
            ("/* open", 0, 1, 1),
            ('"unterminated', 0, 1, 1),
            ('"\\q"', 0, 1, 1),
            ("1__2", 0, 1, 1),
            ("1e+", 0, 1, 1),
            ("1e999", 0, 1, 1),
        ]:
            with self.subTest(text=text):
                result = self.scan(text, "bad.pxl")
                self.assertEqual(len(result["diagnostics"]), 1)
                error = result["diagnostics"][0]
                self.assertEqual(
                    (
                        error["source"],
                        error["phase"],
                        error["start"],
                        error["line"],
                        error["column"],
                    ),
                    ("bad.pxl", "lex", start, line, column),
                )
                self.assertGreater(error["end"], error["start"])

    def test_unicode_classification_is_general_and_checked(self):
        code = """effect fn main() {
 for value in ["A", "中", "9", "😀"] { print(unicodeCategory(value)) }
}"""
        self.assertEqual(
            VM(compile_with(self.bytecode, {"main.pxl": code})[0]).run(),
            ["Lu", "Lo", "Nd", "So"],
        )
        from pixellang.model import PixelError
        invalid = compile_with(self.bytecode, {"main.pxl": 'effect fn main(){print(unicodeCategory("ab"))}'})[0]
        with self.assertRaises(PixelError) as raised:
            VM(invalid).run()
        self.assertEqual(raised.exception.code, "text.invalid_character")

    def test_native_lexer_matches_reference_vm(self):
        with native_driver(self.image) as run:
            for text in ('let 数量=123\n"hello"', '"unterminated'):
                with self.subTest(text=text):
                    self.assertEqual(run(dict(source='test.pxl',text=text),steps=20_000_000,
                        output_chars=SELFHOST_MAX_OUTPUT_CHARS)[0],self.scan(text))
