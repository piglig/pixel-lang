"""Ordinary value storage cannot be changed through raw VM references."""
import unittest

from pixellang.model import PixelError
from pixellang.vm import VM
from tests.bytecode_helpers import op, program


class ImmutableStorageTests(unittest.TestCase):
    def test_raw_instruction_writes_cannot_bypass_value_updates(self):
        cases = [
            ('int[]', [5], [op('LOAD', 0), op('PUSH', 0), op('PUSH', 0), op('INDEX_SET')]),
            ('@0:1', {'value': 5}, [op('LOAD', 0), op('PUSH', 0), op('FIELD_SET', 'value')]),
        ]
        for typ, items, body in cases:
            with self.subTest(typ=typ):
                code = program([*body, op('PUSH', 0), op('RETURN')], typ, 'unit')
                code['constants'][0] = dict(type='int', value=0)
                code['records']['@0:1'] = {'value': 'int'}
                vm = VM(code)
                source = vm.allocate(typ, items, None)
                before = vm.materialize(source)
                vm._call('subject', [source])
                with self.assertRaises(PixelError) as error:
                    for _ in body:
                        vm.step(snapshot=False)
                self.assertEqual(error.exception.code, 'value.immutable')
                self.assertEqual(vm.materialize(source), before)

    def test_packed_arrays_keep_readonly_storage_after_checkpoint_restore(self):
        vm = VM(program([op('LOAD', 0), op('RETURN')]))
        source = vm.allocate('int[]', bytearray([1]) * 4096, None)
        vm.stack.append(source)
        checkpoint = vm.checkpoint()
        vm.restore_checkpoint(checkpoint)
        with self.assertRaisesRegex(PixelError, 'immutable'):
            vm.require_mutable(source, None)
        self.assertEqual(vm.materialize(source), [1] * 4096)


if __name__ == '__main__':
    unittest.main()
