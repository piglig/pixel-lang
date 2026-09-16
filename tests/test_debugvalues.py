import unittest

from pixellang.model import PixelError
from pixellang.workstation import Workstation


class DebugValuesTests(unittest.TestCase):
    def test_enum_payload_and_pattern_binding_names(self):
        service = Workstation()
        state = service.request(
            "build",
            {
                "files": {
                    "main.pxl": """fn main() {
 let choice = Some([10, 20])
 match choice {
  Some(values) => { print(values) }
  None => {}
 }
}"""
                }
            },
        )
        args = {"session": state["session"]}
        while not state["state"]["halted"]:
            previous = state
            state = service.request("step", args)
        state = service.request("seek", {**args, "z": previous["cursor"]})
        variables = service.request(
            "variables", {**args, "z": state["cursor"], "path": ["frame", 0]}
        )["variables"]
        choice = variables[0]
        self.assertIn(".Some", choice["value"])
        payload = service.request(
            "variables", {**args, "z": state["cursor"], "path": choice["path"]}
        )["variables"]
        self.assertEqual([item["name"] for item in payload], ["value"])
        self.assertEqual(payload[0]["indexedVariables"], 2)
        self.assertIn("values", state["frames"][0]["names"])

    def test_nested_values_paging_and_time(self):
        service = Workstation()
        built = service.request(
            "build",
            {
                "files": {
                    "main.pxl": 'record Row { values: [int] }\nfn main() {\nlet row = Row{values: [10, 20, 30]}\nlet data = jsonParse("{\\"nested\\": [true, 42]}")\nprint(1)\n}'
                }
            },
        )
        state = built
        # Stop before final print, retaining initialized locals.
        while not state["state"]["halted"]:
            previous = state
            state = service.request("step", {"session": built["session"]})
        state = service.request(
            "seek", {"session": built["session"], "z": previous["cursor"]}
        )
        args = {"session": built["session"], "z": state["cursor"]}

        def get(path, **extra):
            return service.request("variables", {**args, "path": path, **extra})[
                "variables"
            ]

        locals_ = get(["frame", 0])
        fields = get(locals_[0]["path"])
        values = get(fields[0]["path"], start=1, count=1)
        self.assertEqual(values[0]["value"], "20")
        self.assertEqual(values[0]["name"], "1")
        nested = get(locals_[1]["path"])
        json_items = get(nested[0]["path"])
        self.assertEqual([v["value"] for v in json_items], ["true", "42"])
        service.request("seek", {"session": built["session"], "z": 0})
        with self.assertRaisesRegex(PixelError, "different time"):
            get(["frame", 0])

    def test_breakpoint_validation_and_single_line_loop(self):
        service = Workstation()
        built = service.request(
            "build",
            {
                "files": {
                    "main.pxl": "// comment\nfn main() { while true { print(1) } }"
                }
            },
        )
        args = {"session": built["session"]}
        verified = service.request(
            "breakpoints", {**args, "source": "main.pxl", "lines": [1, 2, 99]}
        )
        self.assertEqual(
            [b["verified"] for b in verified["breakpoints"]], [False, True, False]
        )
        state = service.request("continue", {**args, "breakpoints": {"main.pxl": [2]}})
        self.assertLess(state["cursor"], 30)
        self.assertFalse(state["state"]["halted"])

    def test_recording_source_snapshot_verified(self):
        service = Workstation()
        text = 'import "std/stats.pxl" as stats\nfn main() {\nprint(stats.Summary([1, 2]))\n}'
        result = service.request("build", {"files": {"main.pxl": text}})
        self.assertIn("std/stats.pxl", result["source_project"]["files"])
        recording = service.request("recording", {"session": result["session"]})
        restored = service.request("restore", {"document": recording})
        self.assertEqual(restored["project"]["files"]["main.pxl"], text)
        recording["source_project"]["files"]["main.pxl"] = "fn main() {\nprint(99)\n}"
        with self.assertRaisesRegex(PixelError, "snapshot"):
            service.request("restore", {"document": recording})


if __name__ == "__main__":
    unittest.main()
