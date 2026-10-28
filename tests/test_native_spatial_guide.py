"""The bilingual spatial tutorial is an executable source/PNG contract."""
from tests.native_compiler import native_service
import re
from pathlib import Path
import unittest
from unittest.mock import patch

from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]

class NativeSpatialGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def result(self, operation, **args):
        with patch('pixellang.text.parse_text', side_effect=AssertionError('host frontend forbidden')):
            response = self.compiler.request(dict(version=1, operation=operation, incremental=False, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def test_bilingual_guide_executes_and_preserves_editable_spatial_data(self):
        programs = [re.search(r'```pixellang\n(.*?)\n```',
                             (ROOT / 'docs' / lang / 'guides/spatial-programming.md').read_text(), re.S)[1]
                    for lang in ('zh-CN', 'en')]
        self.assertEqual(programs[0], programs[1])
        files = {'main.pxl': programs[0]}
        expected = [1, 0, 7, 3.0]
        self.assertEqual(VM(self.result('compile', files=files)['bytecode']).run(), expected)
        image = self.result('export-png', files=files)['image']
        self.assertEqual(VM(self.result('compile-png', image=image)['bytecode']).run(), expected)
        recovered = self.result('recover-png', image=image)
        self.assertEqual(VM(self.result('compile', **recovered)['bytecode']).run(), expected)
        recovered['files'] = {name: text.replace('cost: 7', 'cost: 9')
                              for name, text in recovered['files'].items()}
        self.assertEqual(VM(self.result('compile', **recovered)['bytecode']).run(), [1, 0, 9, 3.0])

if __name__ == '__main__':
    unittest.main()
