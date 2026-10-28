import copy
import unittest

from pixellang.codec import validate
from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM, validate_bytecode


class RecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, operation, **args):
        response = self.compiler.request(operation, **args)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def compile_code(self, code):
        return self.request('compile', files={'main.pxl': code})['bytecode']

    def test_nominal_records_and_nested_field_mutation_roundtrip(self):
        files = {
            "main.pxl": 'import "models.pxl" as models\neffect fn main() {\nlet sale = models.Make()\nvar edited = sale\nedited.amount = 55\nvar records: [models.Sale] = [edited]\nrecords[0].labels[0] = "updated"\nlet entries = map[string, models.Sale]{"sale": records[0]}\nprint(entries["sale"])\nprint(sale)\nprint(edited)\n}',
            "models.pxl": 'export record Sale { category: string, amount: int, labels: [string] }\nexport fn Make() -> Sale = Sale{amount: 42, labels: ["old"], category: "books"}\n',
        }
        compiled = self.request('compile', files=files)['bytecode']
        expected = [{"category": "books", "amount": 55, "labels": ["updated"]},
                    {"category": "books", "amount": 42, "labels": ["old"]},
                    {"category": "books", "amount": 55, "labels": ["old"]}]
        self.assertEqual(VM(compiled).run(), expected)
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.request('recover-png', image=image)
        self.assertTrue(any('labels: [string]' in text for text in recovered['files'].values()))
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_rejects_wrong_missing_duplicate_extra_and_nominal_types(self):
        declaration = "record A { value: int }\nrecord B { value: int }\n"
        for expression in [
            "effect fn main() {\nlet a = A{value: true}\n}",
            "effect fn main() {\nlet a = A{}\n}",
            "effect fn main() {\nlet a = A{value: 1, value: 2}\n}",
            "effect fn main() {\nlet a = A{value: 1, extra: 2}\n}",
            "effect fn main() {\nlet a: A = B{value: 1}\n}",
            'effect fn main() {\nvar a: A = A{value: 1}\na.value = "bad"\n}',
            "effect fn main() {\nlet a = A{value: 1}\nprint(a.absent)\n}",
            'effect fn main() {\nlet a = A{value: 1}\nprint(a["value"])\n}',
            "effect fn main() {\nlet a = A{value: 1}\nfor item in a { print(item) }\n}",
        ]:
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                self.compile_code(declaration + expression)
        for declaration in [
            "record A { value: int, value: int }",
            "record A { value: Missing }",
        ]:
            with self.assertRaises(PixelError):
                self.compile_code(declaration)

    def test_private_type_and_unknown_type_rejection(self):
        with self.assertRaisesRegex(PixelError, "private"):
            self.request("compile", files=
                {
                    "main.pxl": 'import "lib.pxl" as lib\neffect fn main() {\nlet a = lib.hidden{value: 1}\n}',
                    "lib.pxl": "record hidden { value: int }",
                }
            )
        with self.assertRaisesRegex(PixelError, "Unknown type"):
            self.compile_code("fn f(n: Missing) { print(n) }\neffect fn main() {}")

    def test_empty_record_and_main_entry(self):
        compiled = self.compile_code("record Empty {}\neffect fn main() { print(Empty{}) }")
        self.assertEqual(VM(compiled).run(), [{}])

    def test_recursive_record_snapshots_gc_and_replay(self):
        code = "record Node { value: int, children: [Node] }\neffect fn main() {\nvar root = Node{value: 42, children: []}\nlet before = root\nroot.children.append(root)\nvar i = 0\nwhile i < 500 { let discard = Node{value: i, children: []}; i += 1 }\nprint(root.children[0].value)\nprint(len(root.children[0].children))\nprint(len(before.children))\n}"
        compiled = self.compiler.debug_project({"main.pxl": code})
        vm = VM(compiled.bytecode)
        self.assertEqual(vm.run(), [42, 0, 0])
        self.assertGreater(vm.collections, 0)
        self.assertLess(len(vm.heap), 130)
        timeline = Timeline(compiled.source, compiled=compiled, checkpoint_interval=32)
        timeline.run()
        for z in [2000, 1300, 2010]:
            reference = timeline.fresh_vm()
            for _ in range(z):
                reference.step(snapshot=False)
            self.assertEqual(timeline.seek(z), reference.state())
        self.assertEqual(
            Timeline.restore(timeline.document()).vm.state(), timeline.vm.state()
        )

    def test_schema_is_executable_structure_and_validated(self):
        files = {'main.pxl': 'record Item { value: int }\neffect fn main() {print(Item{value:7})}'}
        source = self.request('pixels', files=files)['document']
        compiled = self.request('compile', files=files)['bytecode']
        old = copy.deepcopy(source)
        old["versions"] = {"language": "0.2", "encoding": "0.2", "format": "0.2"}
        with self.assertRaisesRegex(PixelError, "version axes"):
            validate(old)
        for mutate in [
            lambda bc: bc["records"].clear(),
            lambda bc: next(iter(bc["records"].values())).update(value="@0:65535"),
            lambda bc: bc.update(vm_version="0.2", language_version="0.2"),
        ]:
            bc = copy.deepcopy(compiled)
            mutate(bc)
            with self.assertRaises(PixelError):
                validate_bytecode(bc)
        recovered = self.request("recover-pixels", document=source)
        recovered["files"][recovered["entry"]] = recovered["files"][recovered["entry"]].replace(
            "value: int", "value: bool"
        )
        with self.assertRaises(PixelError):
            self.request("compile", **recovered)
