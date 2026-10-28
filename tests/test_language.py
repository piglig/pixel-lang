import copy
import json
from io import BytesIO
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from examples.build import examples
from pixellang.builder import Grid, I
from pixellang.codec import load, save
from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]


class NativePixelCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, grid):
        doc = grid.build() if isinstance(grid, Grid) else grid
        return self.compiler.request('compile-pixels', document=doc)['result']['bytecode']

    def execute(self, grid):
        return VM(self.compile(grid)).run()

    def normalize(self, doc):
        return self.compiler.request('normalize-pixels', document=doc)['result']['document']


class LanguageTests(NativePixelCase):
    def test_png_is_source(self):
        from PIL import Image

        doc = examples()["addition"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain.png"
            encoded = self.compiler.request('export-pixel-png', document=doc)['result']['image']
            # Re-encode only pixels; execution cannot depend on PNG text metadata.
            with Image.open(BytesIO(encoded)) as image:
                Image.frombytes('RGBA', image.size, image.convert('RGBA').tobytes()).save(path)
            self.assertEqual(VM(self.compiler.request('compile-png', image=path.read_bytes())['result']['bytecode']).run(), [30])
            with Image.open(path) as image:
                self.assertNotIn('pixellang', image.info)

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
                        self.compile(load(ROOT / "examples" / f"{name}.pixel"))
                    ).run(),
                    expected,
                )

    def test_precedence_and_integer_division(self):
        self.assertEqual(
            self.execute(
                Grid()
                .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
                .row("print", 2, "+", 3, "*", 4, indent=1)
                .row("print", -7, "/", 3, indent=1)
                .row("print", -7, "%", 3, indent=1)
            ),
            [14, -2, -1],
        )

    def test_boolean_logic_short_circuit(self):
        self.assertEqual(
            self.execute(
                Grid()
                .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
                .row("print", False, "and", "(", 1, "/", 0, "==", 0, ")", indent=1)
                .row("print", True, "or", "(", 1, "/", 0, "==", 0, ")", indent=1)
                .row("print", "not", False, indent=1)
            ),
            [False, True, True],
        )

    def test_lexical_shadowing(self):
        self.assertEqual(
            self.execute(
                Grid()
                .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
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
            self.execute(
                Grid()
                .row("fn", I(0), "(", I(1), ":", "bool", ")", "->", "bool")
                .row("return", "not", I(1), indent=1)
                .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
                .row("print", I(0), "(", False, ")", indent=1)
            ),
            [True],
        )

    def test_spatial_position_changes_operand_order(self):
        doc = (
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 10, "-", 20, indent=1)
            .build()
        )
        self.assertEqual(self.execute(doc), [-10])
        operands = [p for p in doc["pixels"] if p["position"] in ([4, 2], [8, 2])]
        operands[0]["position"], operands[1]["position"] = (
            operands[1]["position"],
            operands[0]["position"],
        )
        self.assertEqual(self.execute(doc), [10])

    def test_layout_does_not_change_scope_but_child_links_do(self):
        doc = (
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("if", False, indent=1)
            .row("print", 10, indent=2)
            .row("print", 20, indent=1)
            .build()
        )
        self.assertEqual(self.execute(doc), [20])
        for p in doc["pixels"]:
            if p["position"][1] == 6:
                p["position"][0] += 2
        for link in doc['spatial']['links']:
            for endpoint in ('from', 'to'):
                if link[endpoint][1] == 6:
                    link[endpoint][0] += 2
        doc["dimensions"][0] += 2
        self.assertEqual(self.execute(doc), [20])
        for link in doc['spatial']['links']:
            if link['kind'] == 'child' and link['to'][1] == 6:
                link['from'] = [2, 2]
        self.assertEqual(self.execute(doc), [])

    def test_explicit_child_selects_parent(self):
        grid = (
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("if", False, indent=1)
            .row("print", 99, indent=1)
            .connect("child", (2, 2), (2, 4))
        )
        self.assertEqual(self.execute(grid), [])

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
        self.assertEqual(self.execute(doc), [10])

    def test_canonical_roundtrip(self):
        for name, doc in examples().items():
            if name == "7":
                continue  # Its normalization is included in the bundled module example.
            with self.subTest(name=name):
                normalized = self.normalize(doc)
                self.assertEqual(normalized, self.normalize(normalized))
                self.assertEqual(self.execute(doc), self.execute(normalized))

    def test_layout_and_metadata_normalization(self):
        a = examples()["addition"]
        b = copy.deepcopy(a)
        b["metadata"] = {"display": "irrelevant"}
        b["dimensions"] = [20, 20]
        for p in b["pixels"]:
            p["position"][0] += 7
            p["position"][1] += 9
        for link in b['spatial']['links']:
            for endpoint in ('from', 'to'):
                link[endpoint][0] += 7
                link[endpoint][1] += 9
        self.assertEqual(self.normalize(a), self.normalize(b))

    def test_png_link_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("connections", "factorial"):
                doc = examples()[name]
                path = Path(tmp) / f"{name}.png"
                save(doc, path)
                self.assertEqual(self.execute(doc), self.execute(load(path)))
                self.assertEqual(doc["spatial"], load(path)["spatial"])

    def test_deterministic_build_and_source_immutability(self):
        doc = examples()["factorial"]
        original = copy.deepcopy(doc)
        a = self.compile(doc)
        b = self.compile(doc)
        self.assertEqual(a, b)
        vm = VM(a, trace=True)
        vm.run()
        self.assertEqual(doc, original)
        self.assertEqual(a, b)
        self.assertTrue(vm.trace)
        self.assertIn("span", vm.trace[0])

    def test_recovered_source_preserves_example_semantics(self):
        for name, doc in examples().items():
            if name == "7":
                continue
            with self.subTest(name=name):
                recovered = self.compiler.request('recover-pixels', document=doc)['result']
                bytecode = self.compiler.request('compile', **recovered)['result']['bytecode']
                self.assertEqual(self.execute(doc), VM(bytecode).run())

    def test_random_arithmetic_roundtrip(self):
        rng = random.Random(2026)
        for _ in range(100):
            a, b, c = [rng.randrange(-100, 100) for _ in range(3)]
            doc = (
                Grid()
                .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
                .row("print", a, "+", b, "*", c, indent=1)
                .build()
            )
            self.assertEqual(self.execute(doc), [a + b * c])
            self.assertEqual(self.execute(self.normalize(doc)), [a + b * c])

    def test_all_comparison_operators(self):
        self.assertEqual(
            self.execute(
                Grid()
                .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
                .row("print", 1, "==", 1, indent=1)
                .row("print", 1, "!=", 2, indent=1)
                .row("print", 1, "<", 2, indent=1)
                .row("print", 1, "<=", 1, indent=1)
                .row("print", 2, ">", 1, indent=1)
                .row("print", 2, ">=", 2, indent=1)
            ),
            [True] * 6,
        )

    def test_empty_program(self):
        self.assertEqual(
            self.execute(Grid().row("entry", "effect_fn", I(99), "(", ")", "->", "unit")), []
        )


class ErrorTests(NativePixelCase):
    def assert_error(self, grid, phase, text):
        with self.assertRaises(PixelError) as cm:
            self.execute(grid)
        self.assertEqual(cm.exception.phase, phase)
        self.assertIn(text, str(cm.exception))
        self.assertIsNotNone(cm.exception.span)

    def test_type_error(self):
        self.assert_error(
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, "+", True, indent=1),
            "check",
            "Type mismatch",
        )

    def test_syntax_error(self):
        self.assert_error(
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, "+", indent=1),
            "parse",
            "expression",
        )

    def test_semantic_error(self):
        self.assert_error(
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", I(90), indent=1),
            "bind",
            "Unknown name",
        )

    def test_runtime_error(self):
        self.assert_error(
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 10, "/", 0, indent=1),
            "data",
            "Division by zero",
        )

    def test_missing_return(self):
        self.assert_error(
            Grid()
            .row("effect_fn", I(1), "(", ")", "->", "int")
            .row("print", 1, indent=1)
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit"),
            "check",
            "every path",
        )

    def test_return_outside_function(self):
        self.assert_error(Grid().row("return", 1).row("entry", "effect_fn", I(99), "(", ")", "->", "unit"), "pixel-project", "declarations only")

    def test_duplicate_variable(self):
        self.assert_error(
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("let", I(1), ":", "int", "=", 0, indent=1)
            .row("let", I(1), ":", "int", "=", 1, indent=1),
            "bind",
            "Duplicate",
        )

    def test_scope_escape(self):
        self.assert_error(
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("if", True, indent=1)
            .row("let", I(0), ":", "int", "=", 2, indent=2)
            .row("print", I(0), indent=1),
            "bind",
            "Unknown name",
        )

    def test_unreachable_statement(self):
        self.assert_error(
            Grid()
            .row("effect_fn", I(0), "(", ")", "->", "int")
            .row("return", 1, indent=1)
            .row("print", 1, indent=1)
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit"),
            "check",
            "Unreachable",
        )

    def test_partial_alpha(self):
        doc = (
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, indent=1)
            .build()
        )
        doc["pixels"][0]["rgba"][3] = 128
        self.assert_error(doc, "encoding", "alpha")

    def test_bad_color(self):
        doc = (
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, indent=1)
            .build()
        )
        doc["pixels"][0]["rgba"][0] = 255
        self.assert_error(doc, "encoding", "Unknown RGBA")

    def test_disconnected_region(self):
        doc = (Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, indent=1).build())
        doc['pixels'][-1]['position'][0] = 20
        doc['dimensions'][0] = 21
        self.assert_error(doc, "spatial", "cover every semantic pixel")

    def test_non_block_parent_is_rejected(self):
        self.assert_error(
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, indent=1)
            .row("print", 2, indent=2),
            "spatial",
            "Nested region",
        )

    def test_connection_cycle(self):
        self.assert_error(
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, "+", 2, indent=1)
            .connect("next", (2, 2), (4, 2))
            .connect("next", (4, 2), (6, 2))
            .connect("next", (6, 2), (8, 2))
            .connect("next", (8, 2), (2, 2)),
            "spatial",
            "incoming next link",
        )

    def test_connection_dangling(self):
        self.assert_error(
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, indent=1)
            .connect("next", (2, 2), (6, 2)),
            "spatial",
            "endpoint",
        )

    def test_connection_incomplete(self):
        self.assert_error(
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, "+", 2, indent=1)
            .connect("next", (2, 2), (4, 2)),
            "spatial",
            "cover every semantic pixel",
        )

    def test_duplicate_pixel(self):
        doc = (
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, indent=1)
            .build()
        )
        doc["pixels"].append(copy.deepcopy(doc["pixels"][0]))
        self.assert_error(doc, "encoding", "Duplicate")

    def test_module_cycles(self):
        library = Grid().row("import", I(7)).build()
        g = Grid().row("import", I(7)).row("entry", "effect_fn", I(99), "(", ")", "->", "unit").build()
        with self.assertRaisesRegex(PixelError, "[Cc]yclic"):
            self.compile({**g, 'bundle': dict(version='0.9', entry='main.pxl', modules={'7': library})})

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
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", I(7), ".", I(1), "(", ")", indent=1)
            .build()
        )
        with self.assertRaisesRegex(PixelError, "private"):
            self.compile({**main, 'bundle': dict(version='0.9', entry='main.pxl', modules={'7': lib})})

    def test_instruction_budget_and_trace(self):
        g = (
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("while", True, indent=1)
            .row("print", 1, indent=2)
        )
        vm = VM(self.compile(g.build()), max_steps=20, trace=True)
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
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", I(1), "(", ")", indent=1)
        )
        vm = VM(self.compile(g.build()), max_depth=8)
        with self.assertRaisesRegex(PixelError, "Call stack limit"):
            vm.run()

    def test_overflow(self):
        g = (
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 32767, "*", 32767, "*", 32767, "*", 32767, "*", 32767, indent=1)
        )
        self.assert_error(g, "runtime", "overflow")

    def test_bad_bytecode(self):
        bc = self.compile(
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, indent=1)
            .build()
        )
        bc["functions"][bc["entry"][0]]["instructions"][0]["arg"] = -1
        with self.assertRaisesRegex(PixelError, "Invalid PUSH"):
            VM(bc)

    def test_stack_underflow(self):
        bc = self.compile(
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, indent=1)
            .build()
        )
        bc["functions"][bc["entry"][0]]["instructions"][0] = {"op": "POP"}
        with self.assertRaisesRegex(PixelError, "underflow"):
            VM(bc).run()

    def test_version_rejection(self):
        doc = (
            Grid()
            .row("entry", "effect_fn", I(99), "(", ")", "->", "unit")
            .row("print", 1, indent=1)
            .build()
        )
        doc["versions"]["language"] = "9.0"
        with self.assertRaisesRegex(PixelError, "version"):
            self.compile(doc)


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
            result = cli("inspect", "examples/addition.png", "--stage", "bytecode")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(VM(json.loads(result.stdout)).run(), [30])
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
