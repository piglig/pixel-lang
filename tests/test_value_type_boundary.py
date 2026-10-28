"""Executable types admit value structure and parameter access, not projections."""
import unittest

from pixellang.typeexpr import Type, parse_type, is_value_type
from pixellang.typesys import valid_type
from pixellang.vm import VM, validate_bytecode
from pixellang.model import PixelError
from tests.bytecode_helpers import program, op


class ValueTypeBoundaryTests(unittest.TestCase):
    def test_retired_wrappers_are_rejected_even_when_nested(self):
        for spelling in ('mut<int[]>', 'Frozen<int[]>', 'map<str,mut<int[]>>',
                         'Option<Frozen<int>>', 'fn(inout<mut<int[]>>)->(unit)'):
            with self.subTest(spelling=spelling):
                with self.assertRaises(ValueError):
                    parse_type(spelling)
                self.assertFalse(valid_type(spelling))

    def test_unknown_structural_kinds_cannot_enter_value_types(self):
        for kind in ('mutable', 'frozen', 'unknown'):
            with self.subTest(kind=kind):
                self.assertFalse(is_value_type(Type(kind, args=(Type('scalar', 'int'),))))
        self.assertFalse(is_value_type(Type('array')))

    def test_retired_types_cannot_hide_in_unused_nominal_schemas(self):
        for spelling in ('mut<int[]>', 'Frozen<int[]>', 'Option<mut<int[]>>'):
            for table in ('records', 'enums'):
                with self.subTest(spelling=spelling, table=table):
                    code = program([op('LOAD', 0), op('RETURN')])
                    fields = {'value': spelling}
                    code[table]['@0:1'] = fields if table == 'records' else {'Item': fields}
                    with self.assertRaises(PixelError):
                        validate_bytecode(code)

    def test_value_types_and_scoped_parameter_access_remain_valid(self):
        for spelling in ('map<str,int[]>', 'Option<int[]>',
                         'fn(inout<int[]>)->(unit)', '@0:1<int[]>'):
            with self.subTest(spelling=spelling):
                self.assertTrue(valid_type(spelling))

    def test_legacy_global_mutators_are_not_bytecode_builtins(self):
        for name in ('append', 'extend', 'pop', 'delete', 'freeze', 'thaw'):
            with self.subTest(name=name):
                code = program([op('BUILTIN', name), op('RETURN')])
                with self.assertRaisesRegex(PixelError, 'Unknown builtin'):
                    validate_bytecode(code)

    def test_legacy_executable_versions_and_map_spellings_are_rejected(self):
        code = program([op('LOAD', 0), op('RETURN')])
        code['vm_version'] = code['language_version'] = '0.8'
        with self.assertRaisesRegex(PixelError, 'Unsupported executable'):
            validate_bytecode(code)
        for spelling in ('int{}', 'mut<int{}>', 'fn(int{})->(int)'):
            with self.subTest(spelling=spelling):
                code = program([op('LOAD', 0), op('RETURN')], spelling, spelling)
                with self.assertRaises(PixelError):
                    validate_bytecode(code)

    def test_ordinary_storage_cannot_be_modified_through_raw_references(self):
        vm = VM(program([op('LOAD', 0), op('RETURN')]))
        value = vm.allocate('int[]', [1], None)
        with self.assertRaisesRegex(PixelError, 'immutable'):
            vm.require_mutable(value, None)
        self.assertEqual(vm.materialize(value), [1])

    def test_json_values_use_the_same_independent_update_rules(self):
        from pixellang.value_update import replace_path
        vm = VM(program([op('LOAD', 0), op('RETURN')]))
        parsed = vm.builtin('jsonParse', ['{"items":[1]}'], None)
        original = vm.decode_json(parsed, 'map<str,int[]>', None)
        changed = replace_path(vm, original,
                               [{'index': True}, {'index': True}], ['items', 0], 2, None)
        self.assertEqual(vm.materialize(original), {'items': [1]})
        self.assertEqual(vm.materialize(changed), {'items': [2]})
