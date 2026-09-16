import unittest

from pixellang.workstation import Workstation


class WorkstationTests(unittest.TestCase):
    def setUp(self):
        self.service = Workstation()

    def test_build_run_seek_and_recover(self):
        built = self.service.request(
            "build",
            {
                "files": {
                    "main.pxl": "fn main() {\nlet values = [3, 1, 2]\nprint(values)\n}"
                }
            },
        )
        session = built["session"]
        result = self.service.request("run", {"session": session})
        self.assertEqual(result["output"], ["[3, 1, 2]"])
        self.assertTrue(result["volume"]["voxels"])
        initial = self.service.request("seek", {"session": session, "z": 0})
        self.assertEqual(initial["output"], [])
        self.assertFalse(initial["state"]["halted"])
        restored = self.service.request("recover", {"image": built["image"]})
        self.assertIn("let values", restored["files"]["main.pxl"])
        record = self.service.request("recording", {"session": session})
        self.assertEqual(
            self.service.request("restore", {"document": record})["cursor"], 0
        )

    def test_breakpoint_stops_before_line(self):
        built = self.service.request(
            "build",
            {"files": {"main.pxl": "fn main() {\nlet answer = 42\nprint(answer)\n}"}},
        )
        result = self.service.request(
            "continue", {"session": built["session"], "breakpoints": {"main.pxl": [3]}}
        )
        self.assertEqual(result["location"]["start"]["line"], 3)
        self.assertEqual(result["output"], [])

    def test_large_integer_display_preserved(self):
        built = self.service.request(
            "build",
            {"files": {"main.pxl": "fn main() {\nprint(9223372036854775807)\n}"}},
        )
        result = self.service.request("run", {"session": built["session"]})
        self.assertEqual(result["output"], ["9223372036854775807"])

    def test_breakpoint_does_not_repeat_after_function_return(self):
        built = self.service.request(
            "build",
            {
                "files": {
                    "main.pxl": "fn answer(n: int) -> int = n + 2\nfn main() {\nlet x = 40\nprint(answer(x))\n}"
                }
            },
        )
        args = {"session": built["session"], "breakpoints": {"main.pxl": [4]}}
        paused = self.service.request("continue", args)
        self.assertTrue(paused["line_start"])
        self.assertEqual(paused["output"], [])
        end = self.service.request("continue", args)
        self.assertEqual(end["output"], ["42"])
        self.assertTrue(end["state"]["halted"])
