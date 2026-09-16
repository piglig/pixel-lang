import copy
import unittest

from pixellang.codec import validate
from pixellang.compiler import compile_source
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM, validate_bytecode


def execute(source):
    _, compiled = compile_project({"main.pxl": source})
    return VM(compiled.bytecode).run()


class MapTests(unittest.TestCase):
    def test_mutation_alias_order_and_delete(self):
        self.assertEqual(
            execute(
                'fn main() {\nlet values = map[int]{"世界": 3, "a": 1}\nlet alias = values\nalias["a"] = 42\nvalues["b"] = 9\nprint(keys(values))\nprint(delete(values, "世界"))\nprint(delete(values, "missing"))\nprint(has(alias, "世界"))\nprint(alias)\nprint(len(values))\n}'
            ),
            [["世界", "a", "b"], True, False, False, {"a": 42, "b": 9}, 2],
        )

    def test_nested_values_gc_and_roundtrip(self):
        files = {
            "main.pxl": 'import "lib.pxl" as lib\nfn main() {\nlet values = map[[int]]{"x": [1, 2]}\nlet nested = [map[map[[int]]]{"root": values}]\nlib.Change(values)\nvar i = 0\nwhile i < 500 { let discard = map[[int]]{"x": [i]}; i += 1 }\nprint(nested[0]["root"]["x"])\n}',
            "lib.pxl": 'export fn Change(values: map[[int]]) { values["x"][0] = 42 }',
        }
        doc, compiled = compile_project(files)
        self.assertEqual(VM(compiled.bytecode).run(), [[42, 2]])
        image_doc = decode_picture(encode_picture(doc))
        self.assertEqual(VM(compile_source(image_doc).bytecode).run(), [[42, 2]])
        restored = project_text(image_doc)
        self.assertEqual(
            VM(compile_project(restored["files"], restored["entry"])[1].bytecode).run(),
            [[42, 2]],
        )
        timeline = Timeline(doc)
        timeline.run()
        for z in [2000, 1000, 2001]:
            reference = timeline.fresh_vm()
            for _ in range(z):
                reference.step(snapshot=False)
            self.assertEqual(timeline.seek(z), reference.state())

    def test_type_errors_and_missing_keys(self):
        for source in [
            'fn main() {\nlet x = map[int]{"a": true}\n}',
            "fn main() {\nlet x = map[int]{1: 2}\n}",
            "fn main() {\nlet x = map[int]{}\nx[1] = 4\n}",
            "fn main() {\nlet x = map[int]{}\nappend(x, 4)\n}",
        ]:
            with self.subTest(source=source), self.assertRaises(PixelError):
                execute(source)
        with self.assertRaisesRegex(PixelError, "Missing map key"):
            execute('fn main() {\nlet x = map[int]{}\nprint(x["absent"])\n}')
        with self.assertRaisesRegex(PixelError, "Duplicate map key"):
            execute('fn main() {\nprint(map[int]{"a": 1, "a": 2})\n}')

    def test_new_pixels_and_bytecode_cannot_claim_old_versions(self):
        doc, compiled = compile_project(
            {"main.pxl": 'fn main() {\nprint(map[int]{"a": 1})\n}'}
        )
        doc["versions"] = {"language": "0.2", "encoding": "0.2", "format": "0.2"}
        with self.assertRaisesRegex(PixelError, "version axes"):
            validate(doc)
        bc = copy.deepcopy(compiled.bytecode)
        bc["vm_version"] = bc["language_version"] = "0.2"
        with self.assertRaises(PixelError):
            validate_bytecode(bc)


class RecoverableErrorTests(unittest.TestCase):
    def test_callee_unwinds_operands_and_continues_next_row(self):
        self.assertEqual(
            execute(
                'fn Parse(s: string) -> int = parseInt(s)\nfn main() {\nlet inputs = ["12", "bad", "-3", "9223372036854775808"]\nvar i = 0\nvar total = 0\nwhile i < len(inputs) {\n    try { total = total + Parse(inputs[i]) } catch err { print(err.kind) }\n    i += 1\n}\nprint(total)\n}'
            ),
            ["numeric", "numeric", 9],
        )

    def test_nested_catch_return_and_rethrow(self):
        self.assertEqual(
            execute(
                'fn Get(value: int) -> int {\n    try {\n        if value == 1 { return 42 }\n        fail("input rejected")\n        return 0\n    } catch err { fail("wrapped: " + err.message); return 0 }\n}\nfn main() {\nprint(Get(1))\ntry { print(Get(2)) } catch problem { print(problem.message) }\ntry { print(map[int]{}["missing"]) } catch problem { print(problem.kind) }\n}'
            ),
            [42, "wrapped: input rejected", "collection"],
        )

    def test_resource_budget_and_assertions_are_fatal(self):
        for source, message in [
            (
                'fn main() {\ntry { assert(false) } catch err { print("hidden") }\n}',
                "Assertion failed",
            ),
            (
                'fn main() {\ntry { while true { let x = 1 } } catch err { print("hidden") }\n}',
                "Instruction budget",
            ),
        ]:
            _, compiled = compile_project({"main.pxl": source})
            vm = VM(compiled.bytecode, max_steps=100)
            with self.assertRaisesRegex(PixelError, message):
                vm.run()
            self.assertEqual(vm.output, [])

    def test_image_recovery_and_time_replay_across_handlers(self):
        source = 'fn main() {\nvar i = 0\nwhile i < 40 {\n    try { print(parseInt("invalid")) } catch err { print(i) }\n    i += 1\n}\n}'
        doc, compiled = compile_project({"main.pxl": source})
        restored = project_text(decode_picture(encode_picture(doc)))
        self.assertEqual(execute(restored["files"]["main.pxl"]), list(range(40)))
        vm = VM(compiled.bytecode, trace=True)
        vm.run()
        self.assertEqual(len(vm.trace), vm.steps)
        timeline = Timeline(doc, checkpoint_interval=8)
        timeline.run()
        for z in [3, 9, 42, 80, 10]:
            reference = timeline.fresh_vm()
            for _ in range(z):
                reference.step(snapshot=False)
            self.assertEqual(timeline.seek(z), reference.state())
        self.assertEqual(
            Timeline.restore(timeline.document()).vm.state(), timeline.vm.state()
        )

    def test_catch_variable_does_not_escape_scope(self):
        with self.assertRaisesRegex(PixelError, "Undefined variable"):
            execute(
                "fn main() {\ntry { print(1) } catch err { print(err) }\nprint(err)\n}"
            )


class CollectionIterationTests(unittest.TestCase):
    def test_snapshot_iteration_and_unicode(self):
        self.assertEqual(
            execute(
                'fn main() {\nlet values = [1, 2]\nfor value in values { print(value); append(values, 99) }\nlet entries = map[int]{"a": 1, "b": 2}\nfor key in entries { print(key); delete(entries, key); entries["new"] = 3 }\nfor letter in "猫🌍" { print(letter) }\nprint(values)\nprint(keys(entries))\n}'
            ),
            [1, 2, "a", "b", "猫", "🌍", [1, 2, 99, 99], ["new"]],
        )

    def test_nested_iteration_evaluates_source_once(self):
        self.assertEqual(
            execute(
                'fn Values() -> [int] { print("once"); return [1, 2] }\nfn main() {\nfor a in Values() { for b in [3, 4] { print(a * b) } }\nfor empty in map[int]{} { fail("unreachable") }\n}'
            ),
            ["once", 3, 4, 6, 8],
        )

    def test_type_and_scope_errors(self):
        for source in [
            "fn main() {\nfor n in 2 { print(n) }\n}",
            "fn main() {\nfor n in [1] { print(n) }\nprint(n)\n}",
        ]:
            with self.subTest(source=source), self.assertRaises(PixelError):
                execute(source)
