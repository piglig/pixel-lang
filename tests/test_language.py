import copy
import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from examples.build import examples
from pixellang.builder import Grid, I
from pixellang.canonical import fingerprint
from pixellang.codec import load, save
from pixellang.compiler import compile_file, compile_source, frontend, normalize
from pixellang.model import PixelError
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]


def run(grid, **kwargs):
    doc = grid.build() if isinstance(grid, Grid) else grid
    return VM(compile_source(doc, **kwargs).bytecode).run()


class LanguageTests(unittest.TestCase):
    def test_png_is_source(self):
        from PIL import Image

        doc = examples()["addition"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain.png"
            image = Image.new("RGBA", tuple(doc["dimensions"]))
            for pixel in doc["pixels"]:
                image.putpixel(tuple(pixel["position"]), tuple(pixel["rgba"]))
            image.save(path)
            self.assertEqual(VM(compile_file(path).bytecode).run(), [30])
            self.assertEqual(load(path)["metadata"], {})

    def test_examples(self):
        for name, expected in [
            ("addition", [30]),
            ("factorial", [720]),
            ("loop", [10]),
            ("branch", [42]),
            ("connections", [10]),
            ("modules", [42]),
        ]:
            with self.subTest(name=name):
                self.assertEqual(
                    VM(
                        compile_file(ROOT / "examples" / f"{name}.pixel").bytecode
                    ).run(),
                    expected,
                )

    def test_precedence_and_integer_division(self):
        self.assertEqual(
            run(
                Grid()
                .row("entry", "fn", I(99), "(", ")", "->", "unit")
                .row(2, "+", 3, "*", 4, indent=1)
                .row(-7, "/", 3, indent=1)
                .row(-7, "%", 3, indent=1)
            ),
            [14, -2, -1],
        )

    def test_boolean_logic_short_circuit(self):
        self.assertEqual(
            run(
                Grid()
                .row("entry", "fn", I(99), "(", ")", "->", "unit")
                .row(False, "and", "(", 1, "/", 0, "==", 0, ")", indent=1)
                .row(True, "or", "(", 1, "/", 0, "==", 0, ")", indent=1)
                .row("not", False, indent=1)
            ),
            [False, True, True],
        )

    def test_lexical_shadowing(self):
        self.assertEqual(
            run(
                Grid()
                .row("entry", "fn", I(99), "(", ")", "->", "unit")
                .row("let", I(0), ":", "int", "=", 1, indent=1)
                .row("if", True, indent=1)
                .row("let", I(0), ":", "int", "=", 2, indent=2)
                .row("print", I(0), indent=2)
                .row("print", I(0), indent=1)
            ),
            [2, 1],
        )

    def test_function_bool(self):
        self.assertEqual(
            run(
                Grid()
                .row("fn", I(0), "(", I(1), ":", "bool", ")", "->", "bool")
                .row("return", "not", I(1), indent=1)
                .row("entry", "fn", I(99), "(", ")", "->", "unit")
                .row(I(0), "(", False, ")", indent=1)
            ),
            [True],
        )

    def test_spatial_position_changes_operand_order(self):
        doc = (
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(10, "-", 20, indent=1)
            .build()
        )
        self.assertEqual(run(doc), [-10])
        operands = [p for p in doc["pixels"] if p["position"] in ([2, 2], [6, 2])]
        operands[0]["position"], operands[1]["position"] = (
            operands[1]["position"],
            operands[0]["position"],
        )
        self.assertEqual(run(doc), [10])

    def test_y_axis_changes_scope(self):
        doc = (
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row("if", False, indent=1)
            .row("print", 10, indent=2)
            .row("print", 20, indent=1)
            .build()
        )
        self.assertEqual(run(doc), [20])
        for p in doc["pixels"]:
            if p["position"][1] == 6:
                p["position"][0] += 2
        doc["dimensions"][0] += 2
        self.assertEqual(run(doc), [])

    def test_explicit_child_overrides_indent(self):
        grid = (
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row("if", False, indent=1)
            .row("print", 99, indent=1)
            .connect("child", (2, 2), (2, 4))
        )
        self.assertEqual(run(grid), [])

    def test_explicit_next_overrides_distance(self):
        doc = copy.deepcopy(examples()["connections"])
        for pixel in doc["pixels"]:
            if pixel["position"] == [8, 2]:
                pixel["position"] = [20, 2]
        doc["dimensions"][0] = 21
        for link in doc["spatial"]["links"]:
            for endpoint in ("from", "to"):
                if link[endpoint] == [8, 2]:
                    link[endpoint] = [20, 2]
        self.assertEqual(run(doc), [10])

    def test_canonical_roundtrip(self):
        for name, doc in examples().items():
            with self.subTest(name=name):
                original, _ = frontend(doc)
                normalized = normalize(doc)
                restored, _ = frontend(normalized)
                self.assertEqual(fingerprint(original), fingerprint(restored))
                self.assertEqual(normalized, normalize(normalized))

    def test_layout_and_metadata_normalization(self):
        a = examples()["addition"]
        b = copy.deepcopy(a)
        b["metadata"] = {"display": "irrelevant"}
        b["dimensions"] = [20, 20]
        for p in b["pixels"]:
            p["position"][0] += 7
            p["position"][1] += 9
        self.assertEqual(normalize(a), normalize(b))

    def test_png_link_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("connections", "factorial"):
                doc = examples()[name]
                path = Path(tmp) / f"{name}.png"
                save(doc, path)
                self.assertEqual(run(doc), run(load(path)))
                self.assertEqual(doc["spatial"], load(path)["spatial"])

    def test_deterministic_build_and_source_immutability(self):
        doc = examples()["factorial"]
        original = copy.deepcopy(doc)
        a = compile_source(doc)
        b = compile_source(doc)
        self.assertEqual(a.bytecode, b.bytecode)
        vm = VM(a.bytecode, trace=True)
        vm.run()
        self.assertEqual(doc, original)
        self.assertEqual(a.bytecode, b.bytecode)
        self.assertTrue(vm.trace)
        self.assertIn("span", vm.trace[0])

    def test_optimizer_semantics(self):
        for name, doc in examples().items():
            if name == "7":
                continue
            if any(
                t.kind == "keyword" and t.value == "import"
                for t in __import__("pixellang.codec", fromlist=["validate"]).validate(
                    doc
                )
            ):
                continue
            self.assertEqual(run(doc, optimized=True), run(doc, optimized=False))

    def test_random_arithmetic_roundtrip(self):
        rng = random.Random(2026)
        for _ in range(100):
            a, b, c = [rng.randrange(-100, 100) for _ in range(3)]
            doc = (
                Grid()
                .row("entry", "fn", I(99), "(", ")", "->", "unit")
                .row(a, "+", b, "*", c, indent=1)
                .build()
            )
            self.assertEqual(run(doc), [a + b * c])
            self.assertEqual(run(normalize(doc)), [a + b * c])

    def test_all_comparison_operators(self):
        self.assertEqual(
            run(
                Grid()
                .row("entry", "fn", I(99), "(", ")", "->", "unit")
                .row(1, "==", 1, indent=1)
                .row(1, "!=", 2, indent=1)
                .row(1, "<", 2, indent=1)
                .row(1, "<=", 1, indent=1)
                .row(2, ">", 1, indent=1)
                .row(2, ">=", 2, indent=1)
            ),
            [True] * 6,
        )

    def test_empty_program(self):
        self.assertEqual(
            run(Grid().row("entry", "fn", I(99), "(", ")", "->", "unit")), []
        )


class ErrorTests(unittest.TestCase):
    def assert_error(self, grid, phase, text):
        with self.assertRaises(PixelError) as cm:
            run(grid)
        self.assertEqual(cm.exception.phase, phase)
        self.assertIn(text, str(cm.exception))
        self.assertIsNotNone(cm.exception.span)

    def test_type_error(self):
        self.assert_error(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, "+", True, indent=1),
            "type",
            "Expected int",
        )

    def test_syntax_error(self):
        self.assert_error(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, "+", indent=1),
            "syntax",
            "Unexpected end",
        )

    def test_semantic_error(self):
        self.assert_error(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(I(90), indent=1),
            "semantic",
            "Undefined variable",
        )

    def test_runtime_error(self):
        self.assert_error(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(10, "/", 0, indent=1),
            "data",
            "Division by zero",
        )

    def test_missing_return(self):
        self.assert_error(
            Grid()
            .row("fn", I(1), "(", ")", "->", "int")
            .row("print", 1, indent=1)
            .row("entry", "fn", I(99), "(", ")", "->", "unit"),
            "semantic",
            "every path",
        )

    def test_return_outside_function(self):
        self.assert_error(Grid().row("return", 1), "semantic", "declarations only")

    def test_duplicate_variable(self):
        self.assert_error(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row("let", I(1), ":", "int", "=", 0, indent=1)
            .row("let", I(1), ":", "int", "=", 1, indent=1),
            "semantic",
            "Duplicate",
        )

    def test_scope_escape(self):
        self.assert_error(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row("if", True, indent=1)
            .row("let", I(0), ":", "int", "=", 2, indent=2)
            .row("print", I(0), indent=1),
            "semantic",
            "Undefined",
        )

    def test_unreachable_statement(self):
        self.assert_error(
            Grid()
            .row("fn", I(0), "(", ")", "->", "int")
            .row("return", 1, indent=1)
            .row("print", 1, indent=1)
            .row("entry", "fn", I(99), "(", ")", "->", "unit"),
            "semantic",
            "Unreachable",
        )

    def test_partial_alpha(self):
        doc = (
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, indent=1)
            .build()
        )
        doc["pixels"][0]["rgba"][3] = 128
        self.assert_error(doc, "encoding", "alpha")

    def test_bad_color(self):
        doc = (
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, indent=1)
            .build()
        )
        doc["pixels"][0]["rgba"][0] = 255
        self.assert_error(doc, "encoding", "Unknown RGBA")

    def test_disconnected_region(self):
        self.assert_error(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, indent=1)
            .row("+", 2, x=12, y=2),
            "spatial",
            "Disconnected",
        )

    def test_bad_indentation(self):
        self.assert_error(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, indent=1)
            .row(2, indent=2),
            "spatial",
            "Nested region",
        )

    def test_connection_cycle(self):
        self.assert_error(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, "+", 2, indent=1)
            .connect("next", (2, 2), (4, 2))
            .connect("next", (4, 2), (2, 2)),
            "spatial",
            "cover every",
        )

    def test_connection_dangling(self):
        self.assert_error(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, indent=1)
            .connect("next", (2, 2), (4, 2)),
            "spatial",
            "endpoint",
        )

    def test_connection_incomplete(self):
        self.assert_error(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, "+", 2, indent=1)
            .connect("next", (2, 2), (4, 2)),
            "spatial",
            "disconnected",
        )

    def test_duplicate_pixel(self):
        doc = (
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, indent=1)
            .build()
        )
        doc["pixels"].append(copy.deepcopy(doc["pixels"][0]))
        self.assert_error(doc, "format", "Duplicate")

    def test_module_cycles(self):
        g = Grid().row("import", I(7)).build()
        with self.assertRaisesRegex(PixelError, "Cyclic"):
            compile_source(g, resolver=lambda mid: (g, "7.pixel"))

    def test_module_visibility(self):
        lib = (
            Grid()
            .row("fn", I(1), "(", ")", "->", "int")
            .row("return", 1, indent=1)
            .build()
        )
        main = (
            Grid()
            .row("import", I(7))
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(I(7), ".", I(1), "(", ")", indent=1)
            .build()
        )
        with self.assertRaisesRegex(PixelError, "private"):
            compile_source(main, resolver=lambda mid: (lib, "7.pixel"))

    def test_instruction_budget_and_trace(self):
        g = (
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row("while", True, indent=1)
            .row("print", 1, indent=2)
        )
        vm = VM(compile_source(g.build()).bytecode, max_steps=20, trace=True)
        with self.assertRaisesRegex(PixelError, "budget"):
            vm.run()
        self.assertEqual(vm.steps, 20)
        self.assertTrue(vm.halted)
        self.assertTrue(vm.error["span"])

    def test_call_stack_limit(self):
        g = (
            Grid()
            .row("fn", I(1), "(", ")", "->", "int")
            .row("return", I(1), "(", ")", indent=1)
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(I(1), "(", ")", indent=1)
        )
        vm = VM(compile_source(g.build()).bytecode, max_depth=8)
        with self.assertRaisesRegex(PixelError, "Call stack limit"):
            vm.run()

    def test_overflow(self):
        g = (
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(32767, "*", 32767, "*", 32767, "*", 32767, "*", 32767, indent=1)
        )
        self.assert_error(g, "runtime", "overflow")

    def test_bad_bytecode(self):
        bc = compile_source(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, indent=1)
            .build()
        ).bytecode
        bc["functions"]["main:99"]["instructions"][0]["arg"] = -1
        with self.assertRaisesRegex(PixelError, "Invalid PUSH"):
            VM(bc)

    def test_stack_underflow(self):
        bc = compile_source(
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, indent=1)
            .build()
        ).bytecode
        bc["functions"]["main:99"]["instructions"][0] = {"op": "POP"}
        with self.assertRaisesRegex(PixelError, "underflow"):
            VM(bc).run()

    def test_version_rejection(self):
        doc = (
            Grid()
            .row("entry", "fn", I(99), "(", ")", "->", "unit")
            .row(1, indent=1)
            .build()
        )
        doc["versions"]["language"] = "9.0"
        with self.assertRaisesRegex(PixelError, "version"):
            compile_source(doc)


class CLITests(unittest.TestCase):
    def test_compile_run_inspect_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            bc = Path(tmp) / "a.pxb"
            fmt = Path(tmp) / "a.pixel"

            def cli(*args):
                return subprocess.run(
                    [sys.executable, "-m", "pixellang", *map(str, args)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(
                cli("compile", "examples/addition.png", "-o", bc).returncode, 0
            )
            result = cli("run", bc, "--json")
            self.assertEqual(json.loads(result.stdout)["output"], [30])
            result = cli("inspect", "examples/addition.png", "--stage", "spatial_ast")
            self.assertEqual(len(json.loads(result.stdout)["regions"]), 2)
            self.assertEqual(
                cli("format", "examples/addition.pixel", "-o", fmt).returncode, 0
            )
            self.assertEqual(cli("run", fmt).stdout.strip(), "30")

    def test_failure_exit_and_json(self):
        result = subprocess.run(
            [sys.executable, "-m", "pixellang", "run", "missing.pixel", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("error", json.loads(result.stderr))


if __name__ == "__main__":
    unittest.main()
