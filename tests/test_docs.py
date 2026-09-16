"""Documentation organization must fail closed on missing translations and links."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.check_docs import check


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for lang, other in [('zh-CN', 'en'), ('en', 'zh-CN')]:
            folder = self.root / 'docs' / lang
            folder.mkdir(parents=True)
            (folder / 'README.md').write_text(
                f'# Documentation\n\n[Home](README.md) [Language](../{other}/README.md)\n')

    def test_complete_pairs_pass(self):
        self.assertEqual(check(self.root), [])

    def test_missing_translation_and_local_target_fail(self):
        (self.root / 'docs/en/guide.md').write_text('# Guide\n\n[Missing](absent.md)\n')
        errors = check(self.root)
        self.assertTrue(any('missing translation' in e for e in errors))
        self.assertTrue(any('missing absent.md' in e for e in errors))
        self.assertTrue(any('missing language switch' in e for e in errors))

    def test_historical_output_is_rejected(self):
        (self.root / 'docs/archive').mkdir()
        (self.root / 'docs/en/summary.json').write_text('{}')
        errors = check(self.root)
        self.assertTrue(any('unexpected documentation entry' in e for e in errors))
        self.assertTrue(any('run artifacts' in e for e in errors))

    def test_examples_need_bilingual_readmes_and_valid_links(self):
        folder = self.root / 'examples/demo'
        folder.mkdir(parents=True)
        (folder / 'README.md').write_text('# Demo\n\n[Missing](absent.pxl)\n')
        errors = check(self.root)
        self.assertTrue(any('README requires' in e for e in errors))
        self.assertTrue(any('missing absent.pxl' in e for e in errors))
