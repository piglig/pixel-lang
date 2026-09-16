import unittest

from pixellang.typeexpr import Type, is_value_type, parse_type


class TypeExpressionTests(unittest.TestCase):
    def test_existing_types_are_lossless(self):
        for spelling in [
            "int",
            "float64",
            "str",
            "bool",
            "json",
            "Error",
            "@0:123",
            "@2:65535[]{}",
            "int{}[][]",
        ]:
            with self.subTest(spelling=spelling):
                parsed = parse_type(spelling)
                self.assertEqual(parsed.spelling(), spelling)
                self.assertTrue(is_value_type(parsed))

    def test_function_result_and_container_binding_are_unambiguous(self):
        array_of_functions = parse_type("fn(int)->(str)[]")
        function_returning_array = parse_type("fn(int)->(str[])")
        self.assertNotEqual(array_of_functions, function_returning_array)
        self.assertEqual(array_of_functions.display(), "[fn(int) -> string]")
        self.assertEqual(function_returning_array.display(), "fn(int) -> [string]")
        self.assertEqual(parse_type("fn()->(unit)").display(), "fn()")

    def test_generic_substitution_is_structural_and_scope_checked(self):
        template = parse_type("fn($T[],Option<$T>)->(@0:4<$T,str>{})")
        self.assertFalse(is_value_type(template))
        self.assertTrue(is_value_type(template, {"T"}))
        result = template.substitute({"T": Type("scalar", "float64")})
        self.assertEqual(
            result.spelling(), "fn(float64[],Option<float64>)->(@0:4<float64,str>{})"
        )
        self.assertNotIn("$", result.spelling())
        self.assertIn("$T", template.spelling())
        self.assertTrue(is_value_type(result))

    def test_invalid_or_ambiguous_types_are_rejected(self):
        for text in [
            "",
            "int ",
            "unknown",
            "@65536:0",
            "fn(int)->str",
            "fn(int,)->(bool)",
            "Option<>",
            "int<int>",
            "int" + "[]" * 34,
            "Option<" * 34 + "int" + ">" * 34,
            "Option<" * 20 + "int" + ">" * 20 + "[]" * 20,
        ]:
            with self.subTest(text=text):
                try:
                    value = parse_type(text)
                except ValueError:
                    continue
                self.assertFalse(is_value_type(value))
        for text in [
            "unit",
            "unit[]",
            "fn(unit)->(int)",
            "Error<int>",
            "Option<int,str>",
        ]:
            self.assertFalse(is_value_type(parse_type(text)), text)


if __name__ == "__main__":
    unittest.main()
