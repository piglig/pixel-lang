import unittest

from pixellang.model import PixelError
from pixellang.workstation import Workstation


class DebugValuesTests(unittest.TestCase):
    def test_enum_payload_and_pattern_binding_names(self):
        service = Workstation()
        self.addCleanup(service.close)
        state = service.request(
            "build",
            {
                "files": {
                    "main.pxl": """effect fn main() {
 let choice = Option.Some([10, 20])
 match choice {
  Option.Some(values) => { print(values) }
  Option.None => {}
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
        self.addCleanup(service.close)
        built = service.request(
            "build",
            {
                "files": {
                    "main.pxl": 'record Row { values: [int] }\neffect fn RunTest()->Result[int,Error] {\nlet row = Row{values: [10, 20, 30]}\nlet decoded = jsonParse("{\\"nested\\": [true, 42]}")\nlet data = (decoded)?\nprint(1)\n\n return .Ok(0)\n}\neffect fn main(){match RunTest(){.Ok(done)=>{},.Err(error)=>{print(error)}}}\n'
                }
            },
        )
        state = built
        # Remember the point where RunTest has initialized its locals. main's
        # final frame no longer owns them after the Result-returning call exits.
        local_cursor = None
        while not state["state"]["halted"]:
            state = service.request("step", {"session": built["session"]})
            if local_cursor is None and state["output"] == ["1"]:
                self.assertIn("row", state["frames"][0]["names"])
                self.assertIn("data", state["frames"][0]["names"])
                local_cursor = state["cursor"]
        self.assertIsNotNone(local_cursor)
        state = service.request(
            "seek", {"session": built["session"], "z": local_cursor}
        )
        args = {"session": built["session"], "z": state["cursor"]}

        def get(path, **extra):
            return service.request("variables", {**args, "path": path, **extra})[
                "variables"
            ]

        locals_ = get(["frame", 0])
        names = state["frames"][0]["names"]
        fields = get(locals_[names.index("row")]["path"])
        values = get(fields[0]["path"], start=1, count=1)
        self.assertEqual(values[0]["value"], "20")
        self.assertEqual(values[0]["name"], "1")
        names = state["frames"][0]["names"]
        nested = get(locals_[names.index("data")]["path"])
        json_items = get(nested[0]["path"])
        self.assertEqual([v["value"] for v in json_items], ["true", "42"])
        service.request("seek", {"session": built["session"], "z": 0})
        with self.assertRaisesRegex(PixelError, "different time"):
            get(["frame", 0])

    def test_breakpoint_validation_and_single_line_loop(self):
        service = Workstation()
        self.addCleanup(service.close)
        built = service.request(
            "build",
            {
                "files": {
                    "main.pxl": "// comment\neffect fn main() { while true { print(1) } }"
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
        self.assertTrue(state["line_start"])
        first = state["cursor"]
        for _ in range(3):
            previous = state["cursor"]
            state = service.request("continue", {**args, "breakpoints": {"main.pxl": [2]}})
            self.assertGreater(state["cursor"], previous)
            self.assertLess(state["cursor"] - previous, 30)
            self.assertTrue(state["line_start"])
        second_pass = state["cursor"]
        service.request("seek", {**args, "z": first})
        replayed = service.request("continue", {**args, "breakpoints": {"main.pxl": [2]}})
        self.assertGreater(replayed["cursor"], first)
        self.assertLess(replayed["cursor"], second_pass)
        self.assertTrue(replayed["line_start"])

    def test_recording_source_snapshot_verified(self):
        service = Workstation()
        self.addCleanup(service.close)
        text = 'import "std/stats.pxl" as stats\neffect fn main() {\nprint(stats.Summary([1, 2]))\n}'
        result = service.request("build", {"files": {"main.pxl": text}})
        self.assertIn("std/stats.pxl", result["source_project"]["files"])
        recording = service.request("recording", {"session": result["session"]})
        restored = service.request("restore", {"document": recording})
        self.assertEqual(restored["project"]["files"]["main.pxl"], text)
        recording["source_project"]["files"]["main.pxl"] = "effect fn main() {\nprint(99)\n}"
        with self.assertRaisesRegex(PixelError, "snapshot"):
            service.request("restore", {"document": recording})


if __name__ == "__main__":
    unittest.main()
