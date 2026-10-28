import unittest

from tests.editor_support import NativeEditorCase
from pixellang.model import PixelError
from pixellang.workstation import Workstation


class IncompleteEditorTests(NativeEditorCase):
    def test_navigation_survives_incomplete_statement_and_block(self):
        for damaged in (
            "let unfinished =",
            'let unfinished = "open',
            "let unfinished = unknown( )",
            "let unfinished: int = true",
        ):
            source = "fn main() {\n let amount = 42\n " + damaged + "\n print(amount)\n"
            with self.subTest(damaged=damaged):
                result = self.workstation.request(
                    "definition",
                    {
                        "files": {"main.pxl": source},
                        "source": "main.pxl",
                        "offset": source.rindex("amount"),
                    },
                )
                self.assertEqual(result["start"]["offset"], source.index("amount"))
                with self.assertRaises(PixelError):
                    self.compile({"main.pxl": source})
                with self.assertRaises(PixelError):
                    self.index({"main.pxl": source}).rename(
                        "main.pxl", source.index("amount"), "total", analyze=self.index
                    )

    def test_unrelated_module_error_does_not_disable_navigation(self):
        main = 'import "math.pxl" as math\nfn main() { print(math.square(3)) }'
        files = {
            "main.pxl": main,
            "math.pxl": "export fn square(n: int) -> int = n * n\nfn broken() { let value = }",
        }
        definition = self.workstation.request(
            "definition",
            {"files": files, "source": "main.pxl", "offset": main.index("square")},
        )
        self.assertEqual(definition["source"], "math.pxl")

    def test_recovered_ast_never_becomes_executable(self):
        files = {"main.pxl": "fn main() { let broken =\nprint(42) }"}
        index = self.index(files)
        self.assertTrue(index.diagnostics)
        response = self.workstation.compiler.worker.request(dict(version=1, operation='compile', files=files))
        self.assertEqual(response['status'], 'diagnostics')
        self.assertFalse((response.get('result') or {}).get('bytecode', {}).get('functions'))

    def test_completion_after_invalid_statement_uses_valid_scope(self):
        source = "fn main() { let amount = 42\n let broken =\n am"
        result = self.complete({"main.pxl": source}, "main.pxl", len(source))
        self.assertEqual([item["label"] for item in result], ["amount"])
