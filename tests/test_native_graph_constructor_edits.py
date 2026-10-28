"""Studio constructor edits checked with authoritative native AST projections."""
from tests.native_compiler import native_service
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = '''// 星 🛰️
flow Child(seed: int) step(value: int) -> int {
 state previous: int = seed
 next previous = value
 output previous
}
flow Parent(start: int) step(value: int) -> int {
 instance child = Child(start /* keep */ + 1) // trailing
 output child.step(value)
}
fn main() {}
'''.replace('\n', '\r\n')


class NativeGraphConstructorEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)

    def project(self, text):
        response = self.service.request(dict(version=1, operation='native-graphs', files={'main.pxl': text}, entry='main.pxl'))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['projection']

    def node(self, payload):
        result = subprocess.run(['node', str(ROOT / 'vscode/test/native-constructor-service.js')], input=json.dumps(payload), text=True, capture_output=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_constructor_edits_and_rejections(self):
        projection = self.project(SOURCE)
        for action, reject in [
            ({'kind': 'set-initializers', 'initializers': ['start /* keep */ + 2']}, False),
            ({'kind': 'set-initializers', 'initializers': ['(start + 2)']}, False),
            ({'kind': 'set-initializers', 'initializers': ['((start /* keep */ + 2))']}, False),
            ({'kind': 'set-initializers', 'initializers': ['"wrong type"']}, True),
            ({'kind': 'set-initializers', 'initializers': ['value']}, True),
            ({'kind': 'set-initializers', 'initializers': ['print(1)']}, True),
            ({'kind': 'set-initializers', 'initializers': ['1 / 0']}, True),
            ({'kind': 'set-initializers', 'initializers': ['0)\r\n state injected: int = 0\r\n instance extra = Child(1']}, True),
            ({'kind': 'add-instance', 'name': 'another', 'target': 'Child', 'initializers': ['start + 3']}, False),
            ({'kind': 'add-instance', 'name': 'another', 'target': 'Child', 'initializers': ['"bad"']}, True),
            ({'kind': 'add-instance', 'name': 'another', 'target': 'Child', 'initializers': ['value']}, True),
        ]:
            with self.subTest(action=action):
                payload = dict(text=SOURCE, projection=projection, action=action)
                candidate = self.node(payload)['candidate']
                self.assertIn('// trailing\r\n', candidate)
                self.assertIn('// 星 🛰️\r\n', candidate)
                checked = self.project(candidate)
                self.node(dict(payload, checked=checked, reject=reject))


    def test_supply_missing_creation_list(self):
        source = SOURCE.replace('Child(start /* keep */ + 1)', 'Child')
        payload = dict(text=source, projection=self.project(source),
                       action={'kind': 'set-expression', 'expression': 'Child(start + 4)'})
        candidate = self.node(payload)['candidate']
        self.assertIn('instance child = Child(start + 4) // trailing\r\n', candidate)
        self.node(dict(payload, checked=self.project(candidate), reject=False))

    def test_unfinished_graphs_still_check_initializers(self):
        for suffix in ['', '\n output child.step(value)', '\n state unused: int = "bad"']:
            with self.subTest(suffix=suffix):
                source = SOURCE[:SOURCE.index('flow Parent')] + (
                    'flow Parent(start: int) step(value: int) -> int { '
                    'instance child = Child("wrong")' + suffix + ' }\nfn main() {}'
                )
                projection = self.project(source)
                diagnostics = projection['diagnostics']
                if 'output' not in suffix:
                    self.assertTrue(any(item['phase'] in ('flow', 'flow-type') for item in diagnostics), diagnostics)
                self.assertTrue(any('mismatch' in item['message'].lower() and item['phase'] != 'flow'
                                    for item in diagnostics), diagnostics)
                self.assertFalse(any('requires an explicit runtime instance' in item['message']
                                     for item in diagnostics), diagnostics)
                if '"bad"' in suffix:
                    self.assertTrue(any('mismatch' in item['message'].lower() and
                                        source[item['start']:item['end']] == '"bad"'
                                        for item in diagnostics), diagnostics)


if __name__ == '__main__':
    unittest.main()
