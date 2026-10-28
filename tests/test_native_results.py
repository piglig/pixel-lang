"""Typed domain failures use native enums across callbacks, JSON and PNG."""
from tests.native_compiler import native_service
from pathlib import Path
import unittest

from pixellang.vm import VM

SOURCE = '''import "std/result.pxl" as result
enum PaymentError { InvalidAmount(expected: int, actual: int), AlreadyPaid }
fn Pay(amount: int) -> Result[int, PaymentError] {
 if amount != 100 { return Result::[int, PaymentError].Err(PaymentError.InvalidAmount(100, amount)) }
 return Result::[int, PaymentError].Ok(amount)
}
fn Receipt(amount: int) -> Result[string, PaymentError] = Result::[string, PaymentError].Ok("paid " + string(amount))
effect fn Show(value: Result[string, PaymentError]) {
 match value {
  Result.Ok(receipt) => { print(receipt) },
  Result.Err(error) => {
   match error {
    PaymentError.InvalidAmount(expected, actual) => { print([expected, actual]) },
    PaymentError.AlreadyPaid => { print("already paid") }
   }
  }
 }
}
effect fn main() {
 Show(result.AndThen(Pay(100), Receipt))
 Show(result.AndThen(Pay(50), Receipt))
 let mapped = result.Map(Pay(100), fn(value: int) = value + 2)
 print(mapped == Result::[int, PaymentError].Ok(102))
 let restored = jsonDecode::[Result[int, PaymentError]](jsonStringify(jsonFrom(Pay(50))))
 match restored { .Ok(value) => { print(value == Pay(50)) }, .Err(error) => { assert(false) } }
 let described = result.MapError(Pay(50), fn(error: PaymentError) = "payment rejected")
 match described {
  Result.Ok(value) => { print(value) },
  Result.Err(error) => { print(error) }
 }
}
'''


class NativeResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)
        cls.library = (Path(__file__).resolve().parents[1] / 'pixellang/stdlib/result.pxl').read_text()

    def request(self, operation, **args):
        response = self.service.request(dict(version=1, operation=operation, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def test_typed_failure_composition_json_and_png(self):
        files = {'main.pxl': SOURCE, 'std/result.pxl': self.library}
        expected = ['paid 100', [100, 50], True, True, 'payment rejected']
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), expected)
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_wrong_error_type_and_unhandled_failure_are_rejected(self):
        for body in [
            'return Result::[int, string].Err(42)',
            'let value = Result::[int, string].Ok(1)\nmatch value { Result.Ok(item) => { return value } }',
        ]:
            source = 'import "std/result.pxl" as result\nfn Bad() -> Result[int, string] {\n' + body + '\n}\neffect fn main() {}'
            response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source, 'std/result.pxl': self.library}))
            self.assertNotEqual(response['status'], 'ok', response)
            self.assertNotIn("'phase': 'parse'", str(response))

    def test_short_circuit_and_error_payload_isolation(self):
        source = '''import "std/result.pxl" as result
fn Never(value: int) -> string { assert(false); return "unreachable" }
fn NeverBind(value: int) -> Result[string, [int]] {
 assert(false); return .Ok("unreachable")
}
fn NeverError(error: [int]) -> string { assert(false); return "unreachable" }
effect fn main() {
 let payload = [1]
 let failure = Result::[int, [int]].Err(payload)
 let mapped = result.Map(failure, Never)
 match mapped {
  Result.Ok(value) => { print(value) },
  Result.Err(error) => { var copy = error; copy[0] = 9 }
 }
 print(payload)
 print(result.AndThen(failure, NeverBind) == Result::[string, [int]].Err([1]))
 let success = Result::[int, [int]].Ok(7)
 print(result.MapError(success, NeverError) == Result::[int, string].Ok(7))
}'''
        files = {'main.pxl': source, 'std/result.pxl': self.library}
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), [[1], True, True])

    def test_result_cannot_be_silently_discarded(self):
        prefix = '''import "std/result.pxl" as result
fn Pay() -> Result[int, string] = Result::[int, string].Err("declined")
fn Forward[T](value: T) -> T = value
'''
        for body in ('Pay()', 'Result::[int, string].Ok(1)',
                     'let callback = Pay\ncallback()', 'Forward(Pay())',
                     'let outcome = Pay()\noutcome'):
            with self.subTest(body=body):
                response = self.service.request(dict(version=1, operation='compile', files={
                    'main.pxl': prefix + 'effect fn main() {\n' + body + '\n}',
                    'std/result.pxl': self.library}))
                self.assertNotEqual(response['status'], 'ok', response)
                self.assertIn('Must-use value cannot be discarded', str(response))
        source = prefix + 'effect fn main() { result.Ignore(Pay()); print(42) }'
        self.assertEqual(VM(self.request('compile', files={
            'main.pxl': source, 'std/result.pxl': self.library})['bytecode']).run(), [42])

    def test_result_signature_change_invalidates_importers(self):
        ordinary = 'export record Receipt { amount: int }\nexport fn Pay() -> Receipt = Receipt{amount: 42}'
        files = {'domain.pxl': ordinary,
                 'main.pxl': 'import "domain.pxl" as domain\neffect fn main() { domain.Pay() }'}
        self.assertEqual(VM(self.request('compile', files=files, incremental=True)['bytecode']).run(), [])
        files['domain.pxl'] = ordinary.replace('-> Receipt = Receipt{amount: 42}',
                                               '-> Result[Receipt, Error] = .Ok(Receipt{amount: 42})')
        rejected = self.service.request(dict(version=1, operation='compile', files=files, incremental=True))
        self.assertNotEqual(rejected['status'], 'ok', rejected)
        self.assertIn('Must-use value cannot be discarded', str(rejected))
        files['main.pxl'] = 'import "domain.pxl" as domain\neffect fn main() { match domain.Pay() { .Ok(paid) => { print(paid.amount) }, .Err(error) => { assert(false) } } }'
        self.assertEqual(VM(self.request('compile', files=files)['bytecode']).run(), [42])

    def test_result_contract_survives_format_png_and_recovery(self):
        files = {'main.pxl': 'record Receipt[T] { amount: T }\nfn Pay() -> Result[Receipt[int], Error] = .Ok(Receipt::[int]{amount: 42})\neffect fn main() { match Pay() { .Ok(receipt) => { print(receipt.amount) }, .Err(error) => { assert(false) } } }'}
        formatted = self.request('format', files=files)['text']
        self.assertEqual(self.request('format', files={'main.pxl': formatted})['text'], formatted)
        image = self.request('export-png', files=files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), [42])
        recovered = self.request('recover-png', image=image)
        text = recovered['files'][recovered['entry']]
        self.assertNotIn('mustuse', text)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), [42])
        import re
        function = re.search(r'fn (\w+)\(\)\s*->\s*Result', text).group(1)
        changed = text.replace('effect fn main()', 'effect fn originalMain()')
        changed += f'\neffect fn main() {{ {function}() }}\n'
        response = self.service.request(dict(version=1, operation='compile', files={recovered['entry']: changed}, entry=recovered['entry']))
        self.assertNotEqual(response['status'], 'ok', response)
        self.assertIn('Must-use value cannot be discarded', str(response))

    def test_removed_attribute_is_not_a_keyword_or_declaration(self):
        for source in ('mustuse record Receipt { amount:int }\nfn main() {}',
                       'export mustuse enum E { V }\nfn main() {}'):
            response = self.service.request(dict(version=1, operation='compile', files={'main.pxl': source}))
            self.assertNotEqual(response['status'], 'ok', response)
        source = 'fn mustuse(value:int)->int=value\neffect fn main(){print(mustuse(42))}'
        self.assertEqual(VM(self.request('compile', files={'main.pxl': source})['bytecode']).run(), [42])

    def test_nominal_values_remain_discardable_without_pixel_metadata(self):
        files = {'main.pxl': 'record Receipt { amount: int }\nenum Outcome { Accepted }\neffect fn main() { Receipt{amount: 1}; Outcome.Accepted; print(42) }'}
        document = self.request('pixels', files=files)['document']
        document['metadata'] = {}
        code = self.request('compile-pixels', document=document)['bytecode']
        self.assertEqual(VM(code).run(), [42])


if __name__ == '__main__':
    unittest.main()
