"""Hash key identity must agree with value equality without exposing aliases."""
import unittest
from unittest.mock import patch
from pixellang import map_keys
from pixellang.model import PixelError
from pixellang.vm import VM
from pixellang.spatial_values import CoordinateFrame, SpatialValue, SpatialOrigin
from tests.bytecode_helpers import program, op


class MapKeyTests(unittest.TestCase):
    def machine(self, enums=None, records=None):
        code = program([op('LOAD', 0), op('RETURN')])
        code['enums'].update(enums or {})
        code['records'].update(records or {})
        return VM(code)

    def test_scalar_type_identity_and_zero_normalization(self):
        vm = self.machine()
        self.assertNotEqual(map_keys.token(vm, True, 'bool'), map_keys.token(vm, 1, 'int'))
        positive = map_keys.token(vm, 0.0, 'float64')
        negative = map_keys.token(vm, -0.0, 'float64')
        self.assertEqual(positive, negative)
        self.assertEqual(hash(positive), hash(negative))
        with self.assertRaises(PixelError): map_keys.token(vm, True, 'int')

    def test_enum_identity_is_by_contents_and_nominal_type(self):
        schema = {'Value': {'number': 'int'}}
        vm = self.machine(enums={'@0:0': schema, '@0:1': schema})
        def value(typ): return vm.allocate(typ, {'$variant': 'Value', 'number': 7}, None)
        a, b, other = value('@0:0'), value('@0:0'), value('@0:1')
        before = vm.checkpoint()
        self.assertEqual(map_keys.token(vm, a, '@0:0'), map_keys.token(vm, b, '@0:0'))
        self.assertNotEqual(map_keys.token(vm, a, '@0:0'), map_keys.token(vm, other, '@0:1'))
        self.assertEqual(vm.checkpoint(), before)

    def test_all_variants_checked_and_mutable_records_rejected(self):
        vm = self.machine(enums={'@0:0': {'Empty': {}, 'Mutable': {'items': 'int[]'}}},
                          records={'@0:1': {'number': 'int'}})
        for typ in ('@0:0', '@0:1', 'int[]', 'map<str,int>', 'json', 'fn(int)->(int)'):
            self.assertFalse(map_keys.supports(vm, typ), typ)

    def test_deep_enum_key_hashing_does_not_use_host_recursion(self):
        vm = self.machine(enums={'@0:0': {'End': {}, 'Next': {'child': '@0:0'}}})
        item = vm.allocate('@0:0', {'$variant': 'End'}, None)
        for _ in range(1500):
            item = vm.allocate('@0:0', {'$variant': 'Next', 'child': item}, None)
        key = map_keys.token(vm, item, '@0:0')
        self.assertEqual(len(key), 1501)
        self.assertIsInstance(hash(key), int)

    def test_spatial_origin_is_not_part_of_key_identity(self):
        code = program([op('LOAD', 0), op('RETURN')])
        code['coordinateFrames'] = {'@0:1': 'meters'}
        vm = VM(code)
        frame = CoordinateFrame('@0:1', 'meters')
        first = SpatialValue('Point3', frame, (0.0, 1.0, 2.0), SpatialOrigin('one', 1))
        second = SpatialValue('Point3', frame, (-0.0, 1.0, 2.0), SpatialOrigin('two', 2))
        self.assertEqual(map_keys.token(vm, first, 'Point3<@0:1>'),
                         map_keys.token(vm, second, 'Point3<@0:1>'))

    def test_cycles_cancellation_and_budgets(self):
        vm = self.machine(enums={'@0:0': {'End': {}, 'Next': {'child': '@0:0'}}})
        item = vm.allocate('@0:0', {'$variant': 'End'}, None)
        vm.heap[item.address]['items'] = {'$variant': 'Next', 'child': item}
        with self.assertRaises(PixelError) as error: map_keys.token(vm, item, '@0:0')
        self.assertEqual(error.exception.code, 'value.cycle')
        with patch.object(map_keys, 'MAX_KEY_BYTES', 38):
            with self.assertRaisesRegex(PixelError, 'byte budget'): map_keys.token(vm, 'éé', 'str')
        with patch.object(map_keys, 'MAX_KEY_ITEMS', 0):
            with self.assertRaisesRegex(PixelError, 'item budget'): map_keys.token(vm, 1, 'int')
        vm.cancelled = True
        with self.assertRaisesRegex(PixelError, 'Execution cancelled'): map_keys.token(vm, 1, 'int')


if __name__ == '__main__': unittest.main()
