"""Value captures preserve values without sharing writable binding cells."""
import unittest
from pixellang.model import PixelError
from pixellang.vm import VM, validate_bytecode
from tests.bytecode_helpers import program, op


class ValueCaptureTests(unittest.TestCase):
    def code(self, instructions):
        code = program([op('LOAD', 0), op('RETURN')])
        code['constants'] = [{'type': 'int', 'value': 1}, {'type': 'int', 'value': 9}]
        code['functions']['main'].update(locals=2, instructions=instructions)
        return code

    def test_capture_does_not_box_or_follow_outer_binding(self):
        code = self.code([
            op('PUSH', 0), op('BIND', 0), op('CAPTURE_VALUE', {'slot': 0, 'type': 'int'}),
            op('BIND', 1), op('PUSH', 1), op('STORE', 0),
            op('LOAD', 0), op('PRINT'), op('LOAD', 1), op('PRINT'),
            op('PUSH', 0), op('RETURN')])
        self.assertEqual(VM(code).run(), [9, 1])

    def test_capture_cell_cannot_be_written_and_checkpoint_preserves_rule(self):
        code = self.code([op('PUSH', 1), op('STORE', 1), op('RETURN')])
        vm = VM(code)
        frame = vm.call_stack[0]
        frame.memory[0] = 1
        frame.memory[1] = vm.capture_value(frame, 0)
        saved = vm.checkpoint()
        for replay in (False, True):
            if replay:
                vm.restore_checkpoint(saved)
            with self.assertRaisesRegex(PixelError, 'Captured values are readonly'):
                vm.run()

    def test_array_storage_is_shared_but_binding_is_fresh_and_gc_safe(self):
        vm = VM(self.code([op('RETURN')]), max_heap_objects=3)
        frame = vm.call_stack[0]
        original = vm.allocate('int[]', [1], None)
        frame.memory[0] = original
        first = vm.capture_value(frame, 0)
        frame.memory[1] = first
        second = vm.capture_value(frame, 0)
        self.assertNotEqual(first, second)
        self.assertEqual(frame.memory[0], original)
        self.assertEqual(vm.binding_value(first), original)
        vm.collect([second])
        self.assertEqual(vm.materialize(vm.binding_value(second)), [1])

    def test_closure_invocation_observes_creation_value(self):
        code = self.code([
            op('PUSH', 0), op('BIND', 0), op('CAPTURE_VALUE', 0),
            op('CLOSURE', 'read'), op('BIND', 1),
            op('PUSH', 1), op('STORE', 0),
            op('LOAD', 1), op('INVOKE', 'fn()->(int)'), op('PRINT'),
            op('LOAD', 0), op('PRINT'), op('PUSH', 0), op('RETURN')])
        code['functions']['read'] = dict(params=[], captures=['int'], locals=1,
            result='int', instructions=[op('LOAD', 0), op('RETURN')])
        self.assertEqual(VM(code).run(), [1, 9])

    def test_invalid_capture_is_rejected(self):
        vm = VM(self.code([op('RETURN')]))
        frame = vm.call_stack[0]
        with self.assertRaisesRegex(PixelError, 'uninitialized'):
            vm.capture_value(frame, 0)
        frame.memory[0] = vm.allocate('int[]', [1], None)
        with self.assertRaises(PixelError):
            vm.capture_value(frame, 0, typ='int')
        for operand in [-1, 2, True, {}, {'slot': 0, 'type': 'nonsense'}]:
            with self.subTest(operand=operand), self.assertRaises(PixelError):
                validate_bytecode(self.code([op('CAPTURE_VALUE', operand), op('RETURN')]))

    def test_legacy_shared_capture_opcode_is_rejected(self):
        with self.assertRaises(PixelError):
            validate_bytecode(self.code([op('CAPTURE',0),op('RETURN')]))

    def test_closure_rejects_a_writable_capture_cell(self):
        for effectful in (False, True):
            with self.subTest(effectful=effectful):
                code = self.code([op('RETURN')])
                code['functions']['read'] = dict(
                    params=[], captures=['int'], locals=1, result='int',
                    instructions=[op('LOAD', 0), op('RETURN')])
                if effectful:
                    code['effectFunctions'].append('read')
                vm = VM(code)
                cell = vm.allocate('int', {'value': 1}, None, cell=True)
                typ = 'effect_fn()->(int)' if effectful else 'fn()->(int)'
                with self.assertRaisesRegex(PixelError, 'readonly values'):
                    vm.allocate(typ, {'$function': 'read', '$capture0': cell}, None)
