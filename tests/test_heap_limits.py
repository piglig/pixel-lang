import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM


class HeapLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def bytecode(self, body):
        response = self.compiler.request('compile', files={'main.pxl': 'effect fn main() { ' + body + ' }'})
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['bytecode']

    def test_invalid_limits(self):
        bytecode = self.bytecode('')
        for key in ('max_heap_items', 'max_heap_objects'):
            for value in (0, -1, True, 1.5, '2', None):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(PixelError, 'budget must be positive'):
                    VM(bytecode, **{key: value})

    def test_default_limits(self):
        vm = VM(self.bytecode(''))
        self.assertEqual((vm.max_heap_items, vm.max_heap_objects), (1_000_000, 100_000))

    def test_item_limit_on_array_allocation(self):
        bytecode = self.bytecode('print([1, 2, 3])')
        with self.assertRaisesRegex(PixelError, 'Heap budget exceeded'):
            VM(bytecode, max_heap_items=2).run()
        self.assertEqual(VM(bytecode, max_heap_items=3).run(), [[1, 2, 3]])

    def test_object_limit_with_live_references(self):
        bytecode = self.bytecode('let a = [1]; let b = [2]; print(a); print(b)')
        with self.assertRaisesRegex(PixelError, 'Heap budget exceeded'):
            VM(bytecode, max_heap_objects=1).run()
        self.assertEqual(VM(bytecode, max_heap_objects=2).run(), [[1], [2]])

    def test_growth_and_split_obey_item_limit(self):
        # Value updates retain the old root until the replacement is published.
        for body, peak in (
            ('var a: [int] = [1]; a.append(2); print(a)', 3),
            ('var a: map[string, int] = map[string, int]{"x": 1}; a["y"] = 2; print(a)', 3),
            ('print(split("a,b", ","))', 2),
        ):
            with self.subTest(body=body):
                with self.assertRaisesRegex(PixelError, 'Heap budget exceeded'):
                    VM(self.bytecode(body), max_heap_items=peak-1).run()
                VM(self.bytecode(body), max_heap_items=peak).run()

    def test_gc_reclaims_unreachable_objects_under_custom_limit(self):
        vm = VM(self.bytecode('print([1]); print([2])'), max_heap_objects=1)
        self.assertEqual(vm.run(), [[1], [2]])
        self.assertGreater(vm.collections, 0)

    def test_growing_live_graph_does_not_trigger_constant_interval_full_scans(self):
        vm = VM(self.bytecode('var retained: [[int]] = []; var i = 0; while i < 4096 { retained.append([i]); i+=1 }; print(retained[4095][0])'))
        self.assertEqual(vm.run(), [4095])
        self.assertLess(vm.collections, 20)
        self.assertGreater(vm.collections, 0)

    def test_large_graph_checkpoint_replays_gc_deterministically(self):
        vm = VM(self.bytecode('var retained: [[int]] = []; var i = 0; while i < 300 { retained.append([i]); i+=1 }; print(len(retained))'))
        for _ in range(500):
            vm.step(snapshot=False)
        checkpoint = vm.checkpoint()
        self.assertEqual(vm.run(), [300])
        expected = vm.checkpoint()
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(), [300])
        self.assertEqual(vm.checkpoint(), expected)
