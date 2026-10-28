"""Bounded decompression publishes independent ordinary byte-array values."""
import unittest
import zlib

from pixellang.model import PixelError
from pixellang.vm import VM
from tests.bytecode_helpers import op, program


class TypedDecompressionTests(unittest.TestCase):
    def make_vm(self, target='int[]', size=4096):
        code = program([op('LOAD', 0), op('RETURN')])
        result_type = f'Result<{target},Error>'
        code['enums'] = {result_type: {'Ok': {'value': target}, 'Err': {'error': 'Error'}}}
        code['constants'] = [dict(type='int', value=size), dict(type='int', value=0)]
        code['functions']['main']['instructions'] = [
            op('LOAD', 0), op('PUSH', 0), op('RESULT_BUILTIN', {'name': 'zlibDecompress', 'result': result_type}),
            op('STORE', 0), op('PUSH', 1), op('RETURN')]
        return VM(code, max_heap_items=6000)

    def test_value_storage_and_single_buffer_budget(self):
        vm = self.make_vm()
        frame = vm.call_stack[0]
        frame.memory[0] = vm.allocate('int[]', bytearray(zlib.compress(b'A' * 4096)), None)
        vm.run()
        wrapped = vm.heap[frame.memory[0].address]['items']
        self.assertEqual(wrapped['$variant'], 'Ok')
        result = wrapped['value']
        self.assertEqual(vm.heap[result.address]['items'], bytearray(b'A' * 4096))
        with self.assertRaises(PixelError):
            vm.require_mutable(result, None)

    def test_invalid_targets_rejected_before_execution(self):
        for target in ('str[]', 'mut<int[]>', 'mut<bool[]>', 'int', 'mut<map<str,int>>'):
            with self.subTest(target=target), self.assertRaises(PixelError):
                self.make_vm(target)

    def test_retired_instruction_is_rejected(self):
        from pixellang.vm import validate_bytecode
        code = program([op('LOAD', 0), op('ZLIB_DECOMPRESS', 'int[]'), op('RETURN')])
        with self.assertRaisesRegex(PixelError, 'Unknown instruction'):
            validate_bytecode(code)

    def test_failures_publish_no_partial_buffer(self):
        for encoded, limit in [(b'bad', 10), (zlib.compress(b'hello'), 4),
                               (zlib.compress(b'hello') + b'trailing', 10)]:
            vm = self.make_vm()
            source = vm.allocate('int[]', bytearray(encoded), None)
            before = vm.next_address, vm.heap_items
            with self.assertRaises(PixelError):
                vm.decompress(source, limit, 'int[]', None)
            self.assertEqual((vm.next_address, vm.heap_items), before)
            self.assertEqual(bytes(vm.sequence(source, None)), encoded)

    def test_compression_keeps_original_bytes_when_a_copy_is_updated(self):
        from pixellang.value_update import replace_path
        vm = self.make_vm()
        source = vm.allocate('int[]', [65, 66], None)
        compressed = vm.builtin('zlibCompress', [source], None)
        changed = replace_path(vm, source, [{'index': True}], [0], 90, None)
        self.assertEqual(vm.materialize(source), [65, 66])
        self.assertEqual(vm.materialize(changed), [90, 66])
        self.assertEqual(zlib.decompress(bytes(vm.sequence(compressed, None))), b'AB')
        self.assertTrue(vm.heap[compressed.address]['immutable'])
