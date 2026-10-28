import copy
import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM, validate_bytecode


class CaptureSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, files):
        response = self.compiler.request('compile', files=files)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['bytecode']

    def bytecode(self, instructions, locals_count=2):
        compiled = self.compile({"main.pxl": "effect fn main() { print(1); print(2) }"})
        bc = copy.deepcopy(compiled)
        bc['constants'] = [
            {'type': 'int', 'value': 1}, {'type': 'int', 'value': 2},
            {'type': 'int', 'value': 0},
        ]
        function = bc["functions"][bc["entry"][0]]
        function["locals"] = locals_count
        function["instructions"] = [
            {"op": op, **({"arg": arg} if arg is not None else {})}
            for op, arg in instructions
        ]
        return bc

    def test_capture_snapshot_and_fresh_binding_are_distinct(self):
        bc = self.bytecode(
            [
                ("PUSH", 0),
                ("BIND", 0),
                ("CAPTURE_VALUE", {"slot": 0, "type": "int"}),
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
        self.assertEqual(vm.run(), [1, 1, 1])

    def test_repeated_captures_are_fresh_and_gc_keeps_payload(self):
        compiled = self.compile(
            {"main.pxl": "effect fn main() { let values = [1, 2]; print(values) }"}
        )
        vm = VM(compiled)
        frame = vm.call_stack[-1]
        while frame.memory[0] is None:
            vm.step()
        payload = frame.memory[0]
        first = vm.capture_value(frame, 0)
        second = vm.capture_value(frame, 0)
        self.assertNotEqual(second, first)
        vm.stack.append(first)
        vm.collect([second])
        self.assertIn(payload.address, vm.heap)
        self.assertEqual(vm.binding_value(first), payload)
        frame.memory[0] = None
        vm.stack.pop()
        vm.collect()
        self.assertNotIn(first.address, vm.heap)
        self.assertNotIn(payload.address, vm.heap)

    def test_checkpoint_preserves_capture_value_and_previous_contents(self):
        compiled = self.compile(
            {"main.pxl": "effect fn main() { var value = 1; let other = 2; print(value) }"}
        )
        vm = VM(compiled)
        frame = vm.call_stack[-1]
        while frame.memory[1] is None:
            vm.step()
        cell = vm.capture_value(frame, 0)
        frame.memory[1] = cell
        checkpoint = vm.checkpoint()
        frame.memory[0] = 42
        vm.restore_checkpoint(checkpoint)
        restored = vm.call_stack[-1]
        self.assertNotEqual(restored.memory[0], restored.memory[1])
        self.assertEqual(restored.memory[0], 1)
        self.assertEqual(vm.binding_value(restored.memory[1]), 1)
        with self.assertRaisesRegex(PixelError, "Captured values are readonly"):
            vm.require_mutable(restored.memory[1], None)

    def test_capture_uninitialized_and_bad_operands_rejected(self):
        compiled = self.compile({"main.pxl": "effect fn main() { let value = 1 }"})
        vm = VM(compiled)
        with self.assertRaisesRegex(PixelError, "uninitialized"):
            vm.capture_value(vm.call_stack[-1], 0)
        for op in ["CAPTURE", "BIND"]:
            bc = self.bytecode([(op, 2)])
            with self.subTest(op=op), self.assertRaises(PixelError):
                validate_bytecode(bc)


if __name__ == "__main__":
    unittest.main()
