import unittest

from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM


class HeapLimitTests(unittest.TestCase):
    def bytecode(self, body):
        return compile_project({'main.pxl': 'fn main() { ' + body + ' }'})[1].bytecode

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
        for body in (
            'let a = [1]; append(a, 2); print(a)',
            'let a = map[int]{"x": 1}; a["y"] = 2; print(a)',
            'print(split("a,b", ","))',
        ):
            with self.subTest(body=body):
                with self.assertRaisesRegex(PixelError, 'Heap budget exceeded'):
                    VM(self.bytecode(body), max_heap_items=1).run()
                VM(self.bytecode(body), max_heap_items=2).run()

    def test_gc_reclaims_unreachable_objects_under_custom_limit(self):
        vm = VM(self.bytecode('print([1]); print([2])'), max_heap_objects=1)
        self.assertEqual(vm.run(), [[1], [2]])
        self.assertGreater(vm.collections, 0)

    def test_growing_live_graph_does_not_trigger_constant_interval_full_scans(self):
        vm = VM(self.bytecode('let retained: [[int]] = []; var i = 0; while i < 4096 { append(retained, [i]); i++ }; print(retained[4095][0])'))
        self.assertEqual(vm.run(), [4095])
        self.assertLess(vm.collections, 20)
        self.assertGreater(vm.collections, 0)

    def test_large_graph_checkpoint_replays_gc_deterministically(self):
        vm = VM(self.bytecode('let retained: [[int]] = []; var i = 0; while i < 300 { append(retained, [i]); i++ }; print(len(retained))'))
        for _ in range(500):
            vm.step(snapshot=False)
        checkpoint = vm.checkpoint()
        self.assertEqual(vm.run(), [300])
        expected = vm.checkpoint()
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(), [300])
        self.assertEqual(vm.checkpoint(), expected)
