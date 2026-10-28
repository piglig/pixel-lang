import json
import unittest

from pixellang.compiler_worker import encode_wire, decode_wire, loads_wire


class WorkerWireDecodeTests(unittest.TestCase):
    def test_bottom_up_decoding_preserves_nested_bytes_and_reserved_keys(self):
        cases = [b'\x00\xff', {'$bytes': 'literal'}, {'$escaped': [['x', 1]]},
                 {'rows': [b'png', {'$bytes': b'raw', '$escaped': {'$bytes': 'literal'}}]},
                 {'result': {'constants': [{'value': {'$bytes': 'not an envelope'}}]},
                  'unicode': '空间', 'large': 2**63-1}]
        for value in cases:
            with self.subTest(value=value):
                encoded = json.dumps(encode_wire(value))
                self.assertEqual(loads_wire(encoded), value)
                self.assertEqual(loads_wire(encoded), decode_wire(json.loads(encoded)))

    def test_malformed_byte_envelope_is_rejected(self):
        with self.assertRaises(ValueError):
            loads_wire('{"$bytes":"%%%"}')


if __name__ == '__main__':
    unittest.main()
