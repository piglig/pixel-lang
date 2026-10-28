"""Checked generic operations retain their success type through PNG recovery."""
import unittest
from unittest.mock import patch

from pixellang.compiler_service import CompilerService
from pixellang.vm import VM
from tests.native_compiler import load_native_compiler


class NativeCheckedPNGTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = load_native_compiler(driver=None)

    def test_generic_json_decode_preserves_success_type(self):
        source = '''record Row {amount:int}
fn decode[T](text:string)->Result[T,Error]=jsonDecode::[T](text)
effect fn main(){
 match decode::[Row]("{\\"amount\\":7}") {.Ok(row)=>{print(row.amount)},.Err(error)=>{print(error.code)}}
 match jsonDecode::[Row]("{\\"amount\\":false}") {.Ok(row)=>{print(row.amount)},.Err(error)=>{print(error.path)}}
 print(jsonDecode::[Result[int,string]]("{\\"variant\\":\\"Ok\\",\\"fields\\":{\\"value\\":9}}") == Result::[Result[int,string],Error].Ok(.Ok(9)))
}'''
        # Include a Result as the decoded value: only the outer error channel
        # belongs to jsonDecode, and neither layer may disappear or be added.
        service = CompilerService(self.code)
        def request(operation, **args):
            response = service.request(dict(version=1, operation=operation, **args))
            self.assertEqual(response['status'], 'ok', response)
            return response['result']
        with patch('pixellang.text.parse_text', side_effect=AssertionError('host parser forbidden')):
            files = {'main.pxl': source}
            expected = VM(request('compile', files=files)['bytecode']).run()
            self.assertEqual(expected, [7, '$.amount', True])
            image = request('export-png', files=files)['image']
            self.assertEqual(VM(request('compile-png', image=image)['bytecode']).run(), expected)
            recovered = request('recover-png', image=image)
            self.assertEqual(VM(request('compile', **recovered)['bytecode']).run(), expected)


if __name__ == '__main__':
    unittest.main()
