import copy
import unittest

from pixellang.codec import validate
from pixellang.compiler import compile_source
from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.temporal import Timeline
from pixellang.vm import VM, validate_bytecode


def compile_code(code):
    return compile_project({"main.pxl": code})


class RecordTests(unittest.TestCase):
    def test_nominal_records_and_nested_field_mutation_roundtrip(self):
        files = {
            "main.pxl": 'import "models.pxl" as models\nfn main() {\nlet sale = models.Make()\nlet alias = sale\nalias.amount = 55\nlet records = [sale]\nrecords[0].labels[0] = "updated"\nlet entries = map[models.Sale]{"sale": sale}\nprint(entries["sale"])\n}',
            "models.pxl": 'export record Sale { category: string, amount: int, labels: [string] }\nexport fn Make() -> Sale = Sale{amount: 42, labels: ["old"], category: "books"}\n',
        }
        source, compiled = compile_project(files)
        expected = [{"category": "books", "amount": 55, "labels": ["updated"]}]
        self.assertEqual(VM(compiled.bytecode).run(), expected)
        decoded = decode_picture(encode_picture(source))
        self.assertEqual(VM(compile_source(decoded).bytecode).run(), expected)
        recovered = project_text(decoded)
        self.assertIn("record Sale", recovered["files"]["models.pxl"])
        self.assertEqual(
            VM(compile_project(recovered["files"])[1].bytecode).run(), expected
        )

    def test_rejects_wrong_missing_duplicate_extra_and_nominal_types(self):
        declaration = "record A { value: int }\nrecord B { value: int }\n"
        for expression in [
            "fn main() {\nlet a = A{value: true}\n}",
            "fn main() {\nlet a = A{}\n}",
            "fn main() {\nlet a = A{value: 1, value: 2}\n}",
            "fn main() {\nlet a = A{value: 1, extra: 2}\n}",
            "fn main() {\nlet a: A = B{value: 1}\n}",
            'fn main() {\nlet a = A{value: 1}\na.value = "bad"\n}',
            "fn main() {\nlet a = A{value: 1}\nprint(a.absent)\n}",
            'fn main() {\nlet a = A{value: 1}\nprint(a["value"])\n}',
            "fn main() {\nlet a = A{value: 1}\nfor item in a { print(item) }\n}",
            "fn main() {\nlet a = A{value: 1}\nprint(a == a)\n}",
        ]:
            with self.subTest(expression=expression), self.assertRaises(PixelError):
                compile_code(declaration + expression)
        for declaration in [
            "record A { value: int, value: int }",
            "record A { value: Missing }",
        ]:
            with self.assertRaises(PixelError):
                compile_code(declaration)

    def test_private_type_and_unknown_type_rejection(self):
        with self.assertRaisesRegex(PixelError, "private"):
            compile_project(
                {
                    "main.pxl": 'import "lib.pxl" as lib\nfn main() {\nlet a = lib.hidden{value: 1}\n}',
                    "lib.pxl": "record hidden { value: int }",
                }
            )
        with self.assertRaisesRegex(PixelError, "Unknown record"):
            compile_code("fn f(n: Missing) { print(n) }\nfn main() {}")

    def test_empty_record_and_main_entry(self):
        _, compiled = compile_code("record Empty {}\nfn main() { print(Empty{}) }")
        self.assertEqual(VM(compiled.bytecode).run(), [{}])

    def test_recursive_records_gc_and_cycles(self):
        code = "record Node { value: int, children: [Node] }\nfn main() {\nlet root = Node{value: 42, children: []}\nappend(root.children, root)\nvar i = 0\nwhile i < 500 { let discard = Node{value: i, children: []}; i += 1 }\nprint(root.children[0].value)\ntry { print(root) } catch error { print(error.kind) }\n}"
        source, compiled = compile_code(code)
        vm = VM(compiled.bytecode)
        self.assertEqual(vm.run(), [42, "value"])
        self.assertGreater(vm.collections, 0)
        self.assertLess(len(vm.heap), 130)
        timeline = Timeline(source, checkpoint_interval=32)
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
        source, compiled = compile_code(
            "record Item { value: int }\nfn main() {\nprint(Item{value: 7})\n}"
        )
        old = copy.deepcopy(source)
        old["versions"] = {"language": "0.2", "encoding": "0.2", "format": "0.2"}
        with self.assertRaisesRegex(PixelError, "version axes"):
            validate(old)
        for mutate in [
            lambda bc: bc["records"].clear(),
            lambda bc: next(iter(bc["records"].values())).update(value="@0:65535"),
            lambda bc: bc.update(vm_version="0.2", language_version="0.2"),
        ]:
            bc = copy.deepcopy(compiled.bytecode)
            mutate(bc)
            with self.assertRaises(PixelError):
                validate_bytecode(bc)
        recovered = project_text(source)
        recovered["files"]["main.pxl"] = recovered["files"]["main.pxl"].replace(
            "value: int", "value: bool"
        )
        with self.assertRaises(PixelError):
            compile_project(recovered["files"])
