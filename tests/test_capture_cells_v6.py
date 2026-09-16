import copy
import unittest

from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM, validate_bytecode


class CaptureCellTests(unittest.TestCase):
    def bytecode(self, instructions, locals_count=2):
        _, compiled = compile_project({"main.pxl": "fn main() { print(1); print(2) }"})
        bc = copy.deepcopy(compiled.bytecode)
        function = bc["functions"][bc["entry"][0]]
        function["locals"] = locals_count
        function["instructions"] = [
            {"op": op, **({"arg": arg} if arg is not None else {})}
            for op, arg in instructions
        ]
        return bc

    def test_alias_update_and_fresh_binding_are_distinct(self):
        bc = self.bytecode(
            [
                ("PUSH", 0),
                ("BIND", 0),
                ("CAPTURE", 0),
                ("BIND", 1),
                ("PUSH", 1),
                ("STORE", 0),
                ("LOAD", 1),
                ("PRINT", None),
                ("PUSH", 0),
                ("BIND", 0),
                ("LOAD", 0),
                ("PRINT", None),
                ("LOAD", 1),
                ("PRINT", None),
                ("PUSH", 2),
                ("RETURN", None),
            ]
        )
        vm = VM(bc)
        self.assertEqual(vm.run(), [2, 1, 2])

    def test_repeated_capture_reuses_cell_and_gc_keeps_payload(self):
        _, compiled = compile_project(
            {"main.pxl": "fn main() { let values = [1, 2]; print(values) }"}
        )
        vm = VM(compiled.bytecode)
        frame = vm.call_stack[-1]
        while frame.memory[0] is None:
            vm.step()
        payload = frame.memory[0]
        first = vm.capture_binding(frame, 0)
        self.assertEqual(vm.capture_binding(frame, 0), first)
        vm.collect()
        self.assertIn(payload.address, vm.heap)
        self.assertEqual(vm.binding_value(first), payload)
        frame.memory[0] = None
        vm.collect()
        self.assertNotIn(first.address, vm.heap)
        self.assertNotIn(payload.address, vm.heap)

    def test_checkpoint_preserves_alias_identity_and_previous_contents(self):
        _, compiled = compile_project(
            {"main.pxl": "fn main() { var value = 1; let other = 2; print(value) }"}
        )
        vm = VM(compiled.bytecode)
        frame = vm.call_stack[-1]
        while frame.memory[1] is None:
            vm.step()
        cell = vm.capture_binding(frame, 0)
        frame.memory[1] = cell
        checkpoint = vm.checkpoint()
        vm.heap[cell.address]["items"]["value"] = 42
        vm.restore_checkpoint(checkpoint)
        restored = vm.call_stack[-1]
        self.assertEqual(restored.memory[0], restored.memory[1])
        self.assertEqual(vm.binding_value(restored.memory[0]), 1)

    def test_capture_uninitialized_and_bad_operands_rejected(self):
        _, compiled = compile_project({"main.pxl": "fn main() { let value = 1 }"})
        vm = VM(compiled.bytecode)
        with self.assertRaisesRegex(PixelError, "uninitialized"):
            vm.capture_binding(vm.call_stack[-1], 0)
        for op in ["CAPTURE", "BIND"]:
            bc = self.bytecode([(op, 2)])
            with self.subTest(op=op), self.assertRaises(PixelError):
                validate_bytecode(bc)


if __name__ == "__main__":
    unittest.main()
