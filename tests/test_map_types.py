"""Explicit key/value map types through shared representations."""
import unittest
from pixellang.typeexpr import Type, parse_type, is_value_type, is_assignable, map_parts


class MapTypeTests(unittest.TestCase):
    def test_nested_map_spelling_display_and_substitution(self):
        typ = parse_type('map<$K,map<str,$V[]>>')
        self.assertEqual(typ.spelling(), 'map<$K,map<str,$V[]>>')
        self.assertEqual(typ.display(), 'map[K, map[string, [V]]]')
        self.assertTrue(is_value_type(typ, {'K', 'V'}))
        concrete = typ.substitute({'K': parse_type('int'), 'V': parse_type('bool')})
        self.assertEqual(concrete.spelling(), 'map<int,map<str,bool[]>>')
        self.assertEqual(map_parts(concrete.spelling()), ('int', 'map<str,bool[]>'))

    def test_map_arguments_are_invariant(self):
        first = parse_type('map<int,effect_fn(int)->(int)>')
        second = parse_type('map<int,fn(int)->(int)>')
        self.assertFalse(is_assignable(first, second))
        self.assertFalse(is_assignable(second, first))
        self.assertFalse(is_assignable(parse_type('map<int,str>'), parse_type('map<float64,str>')))

    def test_invalid_arity_and_unit_are_rejected(self):
        for spelling in ('map<>', 'map<int>', 'map<int,str,bool>'):
            with self.subTest(spelling=spelling), self.assertRaises(ValueError): parse_type(spelling)
        self.assertFalse(is_value_type(parse_type('map<unit,int>')))
        self.assertFalse(is_value_type(parse_type('map<int,unit>')))
        self.assertFalse(is_value_type(Type('map')))

    def test_old_seed_spelling_is_rejected_without_reinterpreting_arrays(self):
        for spelling in ('int{}', 'mut<int{}>', 'fn(int{})->(unit)'):
            with self.subTest(spelling=spelling), self.assertRaises(ValueError):
                map_parts(spelling)
        malformed = Type('map', args=(parse_type('int'),))
        self.assertFalse(is_value_type(malformed))
        with self.assertRaises(ValueError):
            malformed.spelling()
        with self.assertRaises(ValueError):
            malformed.display()
        self.assertIsNone(map_parts('map<int,str>[]'))
        self.assertIsNone(map_parts('int'))


if __name__ == '__main__': unittest.main()
