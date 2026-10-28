import unittest

from pixellang.recording import footprint
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM


class PackedArrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, files):
        response = self.compiler.request('compile', files=files)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['bytecode']

    def test_incrementally_grown_buffers_stay_compact_and_preserve_aliases(self):
        compiled = self.compile({'main.pxl': '''effect fn main() {
 var appended: [int] = []
 let alias = appended
 var i = 0
 while i < 100000 { appended.append(i % 256); i += 1 }
 var extended: [int] = []
 let chunk = arrayRepeat([7], 5000)
 i = 0
 while i < 20 { extended.extend(chunk); i += 1 }
 print(len(alias)); print(len(appended)); print(appended[99999]); print(len(extended))
}'''})
        vm = VM(compiled, max_steps=5_000_000)
        self.assertEqual(vm.run(), [0, 100000, 159, 100000])
        self.assertLess(footprint(vm.checkpoint()), 350000)

    def test_growing_signed_arrays_and_self_extension(self):
        compiled = self.compile({'main.pxl': '''effect fn main() {
 var values = arrayRepeat([300], 2048)
 values.extend(values)
 values.append(-1)
 print(len(values)); print(values[4095]); print(values.pop())
 var bytes = arrayRepeat([255], 2048)
 let alias = bytes
 bytes.extend(bytes)
 bytes.extend(bytes)
 bytes.append(-2)
 print(len(alias)); print(alias[2047]); print(len(bytes)); print(bytes[8191]); print(bytes.pop())
}'''})
        self.assertEqual(VM(compiled).run(), [4097, 300, {'variant': 'Some', 'fields': {'value': -1}},
                                               2048, 255, 8193, 255,
                                               {'variant': 'Some', 'fields': {'value': -2}}])

    def test_signed_mutations_preserve_aliases_and_snapshot_iteration(self):
        source = '''effect fn main() {
 var values = arrayRepeat([1, 255], 2048)
 let alias = values
 values[0] = -9223372036854775807
 values.append(9223372036854775807)
 values.extend([-2, 300])
 print(alias[0]); print(values[0]); print(values.pop()); print(values.pop()); print(values.pop())
 print(slice(alias, 0, 2)); print(len(values))
 var bytes = arrayRepeat([3], 4096)
 bytes.extend(bytes)
 print(len(bytes))
 let independent = slice(bytes, 0, 2)
 bytes[0] = 900
 print(independent)
}'''
        compiled = self.compile({'main.pxl': source})
        expected = [1, -9223372036854775807,
                    *({'variant': 'Some', 'fields': {'value': value}}
                      for value in (300, -2, 9223372036854775807)),
                    [1, 255], 4096, 8192, [3, 3]]
        self.assertEqual(VM(compiled).run(), expected)

    def test_byte_arrays_use_bounded_checkpoint_memory(self):
        compiled = self.compile({'main.pxl': 'effect fn main(){ let pixels = arrayRepeat([0, 1, 2, 255], 25000); print(len(pixels)) }'})
        vm = VM(compiled)
        self.assertEqual(vm.run(), [100000])
        self.assertLess(footprint(vm.checkpoint()), 200000)
        self.assertGreaterEqual(vm.heap_items, 100000)

    def test_append_and_extend_promote_independently(self):
        compiled = self.compile({'main.pxl': '''effect fn main() {
 var first = arrayRepeat([0], 4096)
 let alias = first
 first.append(-1)
 print(len(alias)); print(alias[4095]); print(first[4096])
 var second = arrayRepeat([2], 4096)
 second.extend([300, -9])
 print(slice(second, 4095, 4098))
}'''})
        self.assertEqual(VM(compiled).run(), [4096, 0, -1, [2, 300, -9]])

    def test_time_replay_across_storage_promotion(self):
        compiled = self.compiler.debug_project({'main.pxl': '''effect fn main() {
 var values: [int] = []
 values.extend(arrayRepeat([0], 2048))
 values.extend(values)
 values[0] = 300
 print(values[0])
}'''})
        timeline = Timeline(compiled.source, compiled=compiled)
        timeline.run()
        restored = Timeline.restore(timeline.document())
        restored.seek(3)
        restored.seek(len(restored.events))
        self.assertEqual(restored.vm.output, [300])
