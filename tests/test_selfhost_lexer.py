import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.project import compile_project
from pixellang.text import Source, lex
from pixellang.vm import VM
from tests.selfhost_runtime import SELFHOST_MAX_OUTPUT_CHARS

ROOT = Path(__file__).resolve().parents[1] / "selfhost"


class SelfHostedLexerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in ROOT.glob("*.pxl")}
        cls.bytecode = compile_project(cls.files)[1].bytecode

    def scan(self, text, source="test.pxl"):
        vm = VM(
            self.bytecode,
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

    def test_own_sources_match_independent_bootstrap_lexemes(self):
        for name, text in self.files.items():
            with self.subTest(source=name):
                expected = lex(Source(text, name))
                result = self.scan(text, name)
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
        code = """fn main() {
 for value in ["A", "中", "9", "😀"] { print(unicodeCategory(value)) }
 try { unicodeCategory("ab") } catch error { print(error.code) }
}"""
        self.assertEqual(
            VM(compile_project({"main.pxl": code})[1].bytecode).run(),
            ["Lu", "Lo", "Nd", "So", "text.invalid_character"],
        )
