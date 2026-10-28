import copy
import unittest

from pixellang.codec import validate
from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM, validate_bytecode


class NativeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, source):
        return self.compiler.request('compile', files={'main.pxl': source})['result']['bytecode']

    def execute(self, source):
        return VM(self.compile(source)).run()


class MapTests(NativeCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def test_mutation_copy_order_and_remove(self):
        self.assertEqual(
            self.execute(
                'effect fn main() {\nvar values: map[string, int] = map[string, int]{"世界": 3, "a": 1}\nvar alias = values\nalias["a"] = 42\nvalues["b"] = 9\nprint(values.keys())\nprint(values.remove("世界"))\nprint(values.remove("missing"))\nprint(alias.has("世界"))\nprint(alias)\nprint(len(values))\n}'
            ),
            [["世界", "a", "b"], {"variant": "Some", "fields": {"value": 3}},
             {"variant": "None", "fields": {}}, True, {"世界": 3, "a": 42}, 2],
        )

    def test_nested_values_gc_and_roundtrip(self):
        files = {
            "main.pxl": 'import "lib.pxl" as lib\neffect fn main() {\nvar values: map[string, [int]] = map[string, [int]]{"x": [1, 2]}\nlet nested: [map[string, map[string, [int]]]] = [map[string, map[string, [int]]]{"root": values}]\nlib.Change(&values)\nvar i = 0\nwhile i < 500 { let discard = map[string, [int]]{"x": [i]}; i += 1 }\nprint(nested[0]["root"]["x"]); print(values["x"])\n}',
            "lib.pxl": 'export fn Change(values: inout map[string, [int]]) { values["x"][0] = 42 }',
        }
        compiled = self.compiler.debug_project(files)
        self.assertEqual(VM(compiled.bytecode).run(), [[1, 2], [42, 2]])
        image = self.compiler.request('export-png', files=files)['result']['image']
        image_code = self.compiler.request('compile-png', image=image)['result']['bytecode']
        self.assertEqual(VM(image_code).run(), [[1, 2], [42, 2]])
        restored = self.compiler.request('recover-png', image=image)['result']
        rebuilt = self.compiler.debug_project(**restored)
        self.assertEqual(VM(rebuilt.bytecode).run(), [[1, 2], [42, 2]])
        timeline = Timeline(rebuilt.source, compiled=rebuilt)
        timeline.run()
        for z in [2000, 1000, 2001]:
            reference = timeline.fresh_vm()
            for _ in range(z):
                reference.step(snapshot=False)
            self.assertEqual(timeline.seek(z), reference.state())

    def test_type_errors_and_missing_keys(self):
        for source in [
            'effect fn main() {\nlet x = map[string, int]{"a": true}\n}',
            "effect fn main() {\nlet x = map[string, int]{1: 2}\n}",
            "effect fn main() {\nvar x: map[string, int] = map[string, int]{}\nx[1] = 4\n}",
            "effect fn main() {\nvar x: map[string, int] = map[string, int]{}\nx.append(4)\n}",
        ]:
            with self.subTest(source=source), self.assertRaises(PixelError):
                self.execute(source)
        with self.assertRaisesRegex(PixelError, "Missing map key"):
            self.execute('effect fn main() {\nlet x = map[string, int]{}\nprint(x["absent"])\n}')
        with self.assertRaisesRegex(PixelError, "Duplicate map key"):
            self.execute('effect fn main() {\nprint(map[string, int]{"a": 1, "a": 2})\n}')

    def test_new_pixels_and_bytecode_cannot_claim_old_versions(self):
        files = {"main.pxl": 'effect fn main() { print(map[string, int]{"a": 1}) }'}
        compiled = self.compiler.debug_project(files)
        doc = self.compiler.request('pixels', files=files)['result']['document']
        doc["versions"] = {"language": "0.2", "encoding": "0.2", "format": "0.2"}
        with self.assertRaisesRegex(PixelError, "version axes"):
            validate(doc)
        bc = copy.deepcopy(compiled.bytecode)
        bc["vm_version"] = bc["language_version"] = "0.2"
        with self.assertRaises(PixelError):
            validate_bytecode(bc)


class RecoverableErrorTests(NativeCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def test_callee_unwinds_operands_and_continues_next_row(self):
        self.assertEqual(
            self.execute(
                'fn Parse(s: string) -> Result[int, Error] = parseInt(s)\neffect fn main() {\nlet inputs = ["12", "bad", "-3", "9223372036854775808"]\nvar i = 0\nvar total = 0\nwhile i < len(inputs) {\n    match Parse(inputs[i]) { .Ok(value) => { total += value }, .Err(err) => { print(err.kind) } }\n    i += 1\n}\nprint(total)\n}'
            ),
            ["numeric", "numeric", 9],
        )

    def test_nested_result_return_and_error_wrapping(self):
        self.assertEqual(self.execute('fn Get(value: int) -> Result[int,string] {\n if value == 1 { return .Ok(42) }\n return .Err("input rejected")\n}\nfn Wrap(value:int) -> Result[int,string] {\n return match Get(value) { .Ok(number) => .Ok(number), .Err(error) => .Err("wrapped: " + error) }\n}\neffect fn main() {\n for value in [1,2] { match Wrap(value) { .Ok(number) => {print(number)}, .Err(error) => {print(error)} } }\n print(match map[string,int]{}.get("missing") {Option.Some(value)=>"present",Option.None=>"missing"})\n}'), [42, "wrapped: input rejected", "missing"])

    def test_resource_budget_and_assertions_are_fatal(self):
        for source, message in [
            (
                'effect fn main() {\nassert(false); print("hidden")\n}',
                "Assertion failed",
            ),
            (
                'effect fn main() {\nwhile true { let x = 1 }; print("hidden")\n}',
                "Instruction budget",
            ),
        ]:
            compiled = self.compile(source)
            vm = VM(compiled, max_steps=100)
            with self.assertRaisesRegex(PixelError, message):
                vm.run()
            self.assertEqual(vm.output, [])

    def test_image_recovery_and_time_replay_across_result_arms(self):
        source = 'effect fn main() {\nvar i = 0\nwhile i < 40 {\n    match parseInt("invalid") { .Ok(value) => {print(value)}, .Err(err) => {print(i)} }\n    i += 1\n}\n}'
        compiled = self.compiler.debug_project({"main.pxl": source})
        image = self.compiler.request('export-png', files={'main.pxl': source})['result']['image']
        restored = self.compiler.request('recover-png', image=image)['result']
        rebuilt = self.compiler.debug_project(**restored)
        self.assertEqual(VM(rebuilt.bytecode).run(), list(range(40)))
        vm = VM(compiled.bytecode, trace=True)
        vm.run()
        self.assertEqual(len(vm.trace), vm.steps)
        timeline = Timeline(rebuilt.source, compiled=rebuilt, checkpoint_interval=8)
        timeline.run()
        for z in [3, 9, 42, 80, 10]:
            reference = timeline.fresh_vm()
            for _ in range(z):
                reference.step(snapshot=False)
            self.assertEqual(timeline.seek(z), reference.state())
        self.assertEqual(
            Timeline.restore(timeline.document()).vm.state(), timeline.vm.state()
        )

    def test_error_binding_does_not_escape_match_scope(self):
        with self.assertRaisesRegex(PixelError, "Unknown name"):
            self.execute(
                'effect fn main() {\nmatch parseInt("invalid") { .Ok(value) => {print(value)}, .Err(err) => {print(err)} }\nprint(err)\n}'
            )


class CollectionIterationTests(NativeCase):
    def test_snapshot_iteration_and_unicode(self):
        self.assertEqual(
            self.execute(
                'effect fn main() {\nvar values: [int] = [1, 2]\nfor value in values { print(value); values.append(99) }\nvar entries: map[string, int] = map[string, int]{"a": 1, "b": 2}\nfor key in entries { print(key); entries.remove(key); entries["new"] = 3 }\nfor letter in "猫🌍" { print(letter) }\nprint(values)\nprint(entries.keys())\n}'
            ),
            [1, 2, "a", "b", "猫", "🌍", [1, 2, 99, 99], ["new"]],
        )

    def test_nested_iteration_evaluates_source_once(self):
        self.assertEqual(
            self.execute(
                'effect fn Values() -> [int] { print("once"); return [1, 2] }\neffect fn main() {\nfor a in Values() { for b in [3, 4] { print(a * b) } }\nfor empty in map[string, int]{} { assert(false) }\n}'
            ),
            ["once", 3, 4, 6, 8],
        )

    def test_type_and_scope_errors(self):
        for source in [
            "effect fn main() {\nfor n in 2 { print(n) }\n}",
            "effect fn main() {\nfor n in [1] { print(n) }\nprint(n)\n}",
        ]:
            with self.subTest(source=source), self.assertRaises(PixelError):
                self.execute(source)
