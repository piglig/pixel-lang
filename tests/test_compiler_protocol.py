import unittest
from pixellang.compiler_protocol import check_native_output
from pixellang.bootstrap import read_bytecode_stream
from pixellang.model import PixelError


class CompilerProtocolTests(unittest.TestCase):
    def failure(self):
        return {'kind':'compiler-error','source':'main.pxl','error':{
            'kind':'json','code':'json.type_mismatch','message':'Expected array',
            'path':'$.tokens','operation':'jsonDecode','expected':'array','actual':'string'}}

    def test_failure_after_partial_output_preserves_error_fields(self):
        with self.assertRaises(PixelError) as raised:
            check_native_output(['already emitted',self.failure()])
        error=raised.exception
        self.assertEqual(error.phase,'compile')
        self.assertEqual(error.fields(),self.failure()['error'])
        self.assertEqual(error.to_dict()['span']['source'],'main.pxl')

    def test_bytecode_transport_reports_native_failure(self):
        with self.assertRaisesRegex(PixelError,'Expected array'):
            read_bytecode_stream([self.failure()])

    def test_malformed_failure_is_protocol_error(self):
        value=self.failure();value['error']['message']=17
        with self.assertRaises(PixelError) as raised:
            check_native_output([value])
        self.assertEqual(raised.exception.code,'service.invalid-response')

    def test_normal_outputs_are_unchanged(self):
        values=['text',{}, {'kind':'header','protocol':1}]
        self.assertIsNone(check_native_output(values))
        self.assertEqual(values,['text',{}, {'kind':'header','protocol':1}])

if __name__=='__main__':unittest.main()
