"""Expression exits preserve caller operands and release only abandoned borrows."""
import unittest

from pixellang.model import PixelError
from pixellang.vm import VM, validate_bytecode
from tests.bytecode_helpers import op, program


def fixture(body):
    code = program([])
    code['constants'] = [dict(type='int', value=n) for n in (1, 10, 99)]
    code['functions'].pop('subject')
    code['functions']['main'].update(locals=3, instructions=body)
    code['functions']['edit'] = dict(params=['inout<int>'], locals=1, result='int', instructions=[
        op('LOAD', 0), op('PUSH', 0), op('ADD'), op('STORE', 0), op('LOAD', 0), op('RETURN')])
    return code


def borrowed_fixture():
    return fixture([
        op('PUSH', 0), op('BIND', 0), op('PUSH', 1), op('BIND', 1),
        op('ACCESS', dict(slot=0, type='int', path=[])),
        op('STACK_DEPTH'), op('STORE', 2),
        op('ACCESS', dict(slot=1, type='int', path=[])), op('PUSH', 2), op('UNWIND', 2),
        op('CALL', 'edit'), op('PRINT'),
        op('ACCESS', dict(slot=1, type='int', path=[])), op('CALL', 'edit'), op('PRINT'),
        op('PUSH', 0), op('RETURN')])


class OperandUnwindTests(unittest.TestCase):
    def test_return_abandons_pending_operands_but_preserves_caller_stack(self):
        code = fixture([op('PUSH', 2), op('CALL', 'early'), op('PRINT'), op('PRINT'), op('PUSH', 0), op('RETURN')])
        code['functions']['early'] = dict(params=[], locals=0, result='int', instructions=[
            op('PUSH', 1), op('PUSH', 0), op('RETURN')])
        self.assertEqual(VM(code).run(), [1, 99])

    def test_unwind_releases_inner_pending_borrow_and_preserves_outer_argument(self):
        vm = VM(borrowed_fixture())
        self.assertEqual(vm.run(), [2, 11])
        self.assertEqual(vm.value_accesses.entries, {})

    def test_unwind_depth_is_relative_to_current_call_frame(self):
        code = fixture([op('PUSH', 2), op('CALL', 'inner'), op('PRINT'), op('PRINT'), op('PUSH', 0), op('RETURN')])
        code['functions']['inner'] = dict(params=[], locals=1, result='int', instructions=[
            op('STACK_DEPTH'), op('STORE', 0), op('PUSH', 1), op('UNWIND', 0),
            op('PUSH', 0), op('RETURN')])
        self.assertEqual(VM(code).run(), [1, 99])

    def test_claimed_parameter_cannot_be_discarded_as_pending_argument(self):
        vm = VM(fixture([op('PUSH', 0), op('RETURN')]))
        caller = vm.call_stack[0]
        caller.memory[0] = 1
        access = vm.value_accesses.begin(vm, caller, 0)
        vm._call('edit', [access])
        with self.assertRaisesRegex(PixelError, 'Cannot discard an active inout parameter'):
            vm.value_accesses.discard_pending(vm, vm.call_stack[-1], [access])

    def test_checkpoint_replays_depth_marker_and_borrow_cleanup(self):
        vm = VM(borrowed_fixture())
        while vm.call_stack[-1].pc != 9:
            vm.step(snapshot=False)
        checkpoint = vm.checkpoint()
        vm.collect()
        expected = vm.run()
        finished = vm.checkpoint()
        vm.restore_checkpoint(checkpoint)
        vm.collect()
        self.assertEqual(vm.run(), expected)
        self.assertEqual(vm.checkpoint(), finished)

    def test_return_releases_pending_reborrow_after_publishing_prior_write(self):
        code = fixture([op('PUSH', 0), op('BIND', 0),
            op('ACCESS', dict(slot=0, type='int', path=[])), op('CALL', 'early'),
            op('PRINT'), op('LOAD', 0), op('PRINT'),
            op('ACCESS', dict(slot=0, type='int', path=[])), op('CALL', 'edit'), op('PRINT'),
            op('PUSH', 0), op('RETURN')])
        code['functions']['early'] = dict(params=['inout<int>'], locals=1, result='int', instructions=[
            op('PUSH', 1), op('STORE', 0), op('ACCESS', dict(slot=0, type='int', path=[])),
            op('PUSH', 2), op('PUSH', 0), op('RETURN')])
        vm = VM(code)
        self.assertEqual(vm.run(), [1, 10, 11])
        self.assertEqual(vm.value_accesses.entries, {})

    def test_invalid_depths_cannot_unwind_caller_operands(self):
        for depth in (-1, True, 1, None, '0'):
            with self.subTest(depth=depth):
                vm = VM(fixture([op('UNWIND', 0), op('PUSH', 0), op('RETURN')]))
                vm.memory[0] = depth
                with self.assertRaisesRegex(PixelError, 'Invalid operand unwind depth'):
                    vm.run()

    def test_unwind_operand_requires_valid_local_slot(self):
        for slot in (-1, True, 3, '0'):
            with self.subTest(slot=slot):
                with self.assertRaisesRegex(PixelError, 'Invalid UNWIND operand'):
                    validate_bytecode(fixture([op('UNWIND', slot), op('PUSH', 0), op('RETURN')]))

    def test_duplicated_retained_borrow_cannot_be_released_by_unwind(self):
        code = fixture([op('PUSH', 0), op('BIND', 0),
            op('ACCESS', dict(slot=0, type='int', path=[])), op('STACK_DEPTH'), op('STORE', 2),
            op('DUP'), op('UNWIND', 2), op('PUSH', 0), op('RETURN')])
        with self.assertRaisesRegex(PixelError, 'Cannot unwind a duplicated inout access'):
            VM(code).run()

if __name__ == '__main__':
    unittest.main()
