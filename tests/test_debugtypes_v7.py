import unittest

from pixellang.workstation import Workstation


class DebugTypeDisplayTests(unittest.TestCase):
    def test_nominal_containers_callbacks_and_reverse_values(self):
        service = Workstation()
        state = service.request(
            "build",
            {
                "files": {
                    "rows.pxl": "export record Row { amount: int }",
                    "main.pxl": """import "rows.pxl" as rows
fn main() {
 let values = [rows.Row{amount: 2}]
 let option = Some(values)
 let choose = fn(row: rows.Row) -> string { return string(row.amount) }
 print(choose(values[0]))
}""",
                }
            },
        )
        session = state["session"]
        while not state["state"]["halted"]:
            previous = state
            state = service.request("step", {"session": session})
        args = {"session": session, "z": previous["cursor"], "path": ["frame", 0]}
        service.request("seek", {"session": session, "z": previous["cursor"]})
        values = service.request("variables", args)["variables"]
        self.assertEqual(
            [v["type"] for v in values],
            ["[rows.Row]", "Option[[rows.Row]]", "fn(rows.Row) -> string"],
        )
        self.assertTrue(all("@1:" not in v["value"] for v in values))
        service.request("seek", {"session": session, "z": 0})
        service.request("seek", {"session": session, "z": previous["cursor"]})
        self.assertEqual(service.request("variables", args)["variables"], values)
