"""Native formatting preserves syntax, comments and exact literal spellings."""
from tests.native_compiler import native_service
import unittest

from pixellang.vm import VM


class NativeFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)

    def request(self, operation, source):
        response = self.service.request(dict(version=1, operation=operation, files={'main.pxl': source}))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def format(self, source):
        result = self.request('format', source)['text']
        self.assertEqual(self.request('format', result)['text'], result)
        return result

    def test_comments_literals_generic_functions_maps_and_execution(self):
        source = '''// Header: 世界 { }
frame W=meters
fn Copy[K:Hashable,V](value:map[K,V])->map[K,V]=value
effect fn main(){
/* block
  literal spacing } [ */ let punctuation="}"
let table=map[int,string]{1:"one"}
let amount=1_000
print(Copy(table)[1]) // Keep this comment.
print(punctuation)
print(-amount)
}
'''
        formatted = self.format(source)
        self.assertIn('/* block\n  literal spacing } [ */', formatted)
        self.assertIn('// Header: 世界 { }', formatted)
        self.assertIn('// Keep this comment.', formatted)
        self.assertIn('let amount = 1_000', formatted)
        self.assertIn('\n    print(punctuation)\n', formatted)
        self.assertIn('Copy[K: Hashable, V]', formatted)
        for text in (source, formatted):
            self.assertEqual(VM(self.request('compile', text)['bytecode']).run(), ['one', '}', -1000])

    def test_layout_and_unavailable_imports_do_not_require_typechecking(self):
        source = 'import "not-installed.pxl" as lib\r\nfn main(){\r\nlet xs=[\r\n1,\r\n2\r\n]\r\nlib.use(xs)\r\n}\r\n'
        formatted = self.format(source)
        self.assertNotIn('\r', formatted)
        self.assertIn('let xs = [\n        1,\n        2\n    ]', formatted)
        self.assertEqual(self.format(''), '')
        self.assertEqual(self.format('// only a comment'), '// only a comment\n')

    def test_invalid_syntax_reports_original_offsets(self):
        source = 'fn main(){\nlet xs=map[int]{}\n}'
        response = self.service.request(dict(version=1, operation='format', files={'main.pxl': source}))
        self.assertEqual(response['status'], 'diagnostics', response)
        self.assertEqual(response['diagnostics'][0]['line'], 2)
        self.assertEqual(response['diagnostics'][0]['source'], 'main.pxl')

    def test_missing_entry_is_a_bounded_request_error(self):
        response = self.service.request(dict(version=1, operation='format', files={'other.pxl': ''}))
        self.assertNotEqual(response['status'], 'ok')
        self.assertIn('Entry must name a supplied source file', str(response))


if __name__ == '__main__':
    unittest.main()
