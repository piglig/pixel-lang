import unittest
from unittest.mock import patch

from pixellang import jsonvalue
from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.vm import VM


class JsonBudgetTests(unittest.TestCase):
    def test_object_member_names_do_not_consume_value_nodes(self):
        with patch.object(jsonvalue, 'MAX_JSON_NODES', 5):
            value = jsonvalue.from_host({'a': 1, 'b': 2, 'c': 3, 'd': 4})
            self.assertEqual(jsonvalue.parse(jsonvalue.stringify(value)), value)
            with self.assertRaisesRegex(PixelError, 'structure budget'):
                jsonvalue.parse('{"a":1,"b":2,"c":3,"d":4,"e":5}')

    def test_names_keep_unicode_validation_and_values_keep_depth_limits(self):
        with self.assertRaisesRegex(PixelError, 'Unicode scalar'):
            jsonvalue.parse('{"\\ud800":1}')
        with patch.object(jsonvalue, 'MAX_JSON_DEPTH', 1):
            value = jsonvalue.parse('{"valid":1}')
            self.assertEqual(jsonvalue.parse(jsonvalue.stringify(value)), value)
            with self.assertRaisesRegex(PixelError, 'structure budget'):
                jsonvalue.parse('{"nested":{"value":1}}')

    def test_vm_json_conversion_and_typed_decode_share_the_boundary(self):
        code = compile_project({'main.pxl': '''fn main() {
 let value = jsonDecode[map[int]](input())
 print(jsonStringify(jsonFrom(value)))
}'''})[1].bytecode
        payload = '{"a":1,"b":2,"c":3,"d":4}'
        with patch.object(jsonvalue, 'MAX_JSON_NODES', 5):
            self.assertEqual(VM(code, input_text=payload).run(), [payload])


if __name__ == '__main__':
    unittest.main()
