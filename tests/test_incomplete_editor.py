import unittest

from pixellang.editor import SymbolIndex, complete
from pixellang.model import PixelError
from pixellang.project import build_project, compile_project
from pixellang.workstation import Workstation


class IncompleteEditorTests(unittest.TestCase):
    def test_navigation_survives_incomplete_statement_and_block(self):
        for damaged in (
            "let unfinished =",
            'let unfinished = "open',
            "let unfinished = unknown( )",
            "let unfinished: int = true",
        ):
            source = "fn main() {\n let amount = 42\n " + damaged + "\n print(amount)\n"
            with self.subTest(damaged=damaged):
                result = Workstation().request(
                    "definition",
                    {
                        "files": {"main.pxl": source},
                        "source": "main.pxl",
                        "offset": source.rindex("amount"),
                    },
                )
                self.assertEqual(result["start"]["offset"], source.index("amount"))
                with self.assertRaises(PixelError):
                    compile_project({"main.pxl": source})
                with self.assertRaises(PixelError):
                    SymbolIndex({"main.pxl": source}).rename(
                        "main.pxl", source.index("amount"), "total"
                    )

    def test_unrelated_module_error_does_not_disable_navigation(self):
        main = 'import "math.pxl" as math\nfn main() { print(math.square(3)) }'
        files = {
            "main.pxl": main,
            "math.pxl": "export fn square(n: int) -> int = n * n\nfn broken() { let value = }",
        }
        definition = Workstation().request(
            "definition",
            {"files": files, "source": "main.pxl", "offset": main.index("square")},
        )
        self.assertEqual(definition["source"], "math.pxl")

    def test_recovered_ast_never_becomes_executable(self):
        files = {"main.pxl": "fn main() { let broken =\nprint(42) }"}
        project = build_project(files, analysis=True, tolerant=True)
        self.assertTrue(project["errors"])
        with self.assertRaisesRegex(PixelError, "analysis only"):
            build_project(files, tolerant=True)

    def test_completion_after_invalid_statement_uses_valid_scope(self):
        source = "fn main() { let amount = 42\n let broken =\n am"
        result = complete({"main.pxl": source}, "main.pxl", "main.pxl", len(source))
        self.assertEqual([item["label"] for item in result], ["amount"])
