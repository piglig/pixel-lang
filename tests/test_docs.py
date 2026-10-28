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

    def test_heading_links_check_duplicates_and_ignore_code(self):
        page = self.root / 'docs/en/README.md'
        page.write_text(page.read_text() + '\n## Values\n\n## Values\n'
                        '\n[Second](#values-1)\n\n```text\n## Not a heading\n```\n')
        self.assertEqual(check(self.root), [])
        page.write_text(page.read_text() + '\n[Missing](#not-a-heading)\n')
        self.assertTrue(any('missing heading' in e for e in check(self.root)))

    def test_root_contributing_and_html_images_are_checked(self):
        (self.root / 'CONTRIBUTING.md').write_text(
            '# Contributing\n\n[Guide](missing.md)\n<img src="missing.png">\n')
        errors = check(self.root)
        self.assertTrue(any('missing missing.md' in e for e in errors))
        self.assertTrue(any('missing image missing.png' in e for e in errors))

    def test_unclosed_fences_and_multiple_titles_fail(self):
        page = self.root / 'docs/en/README.md'
        page.write_text(page.read_text() + '\n# Another title\n\n```sh\npixel doctor\n')
        errors = check(self.root)
        self.assertTrue(any('expected one page title' in e for e in errors))
        self.assertTrue(any('unclosed code fence' in e for e in errors))
