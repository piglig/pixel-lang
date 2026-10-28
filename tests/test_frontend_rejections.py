"""Text grammar boundaries and ordinary identifier names."""
import unittest
from pixellang.model import PixelError
from pixellang.text import parse_text


class FrontendRejectionTests(unittest.TestCase):
    def test_try_and_catch_blocks_are_rejected(self):
        for source in (
            'fn main(){try {let value=1} catch error {let fallback=2}}',
            'fn main(){catch error {let value=1}}',
        ):
            with self.subTest(source=source), self.assertRaises(PixelError):
                parse_text(source)

    def test_retired_words_can_name_user_values(self):
        tree = parse_text('fn identity(value:int)->int=value\nfn main(){let try=identity(1);let catch=try}')
        self.assertEqual([node.data['name'] for node in tree.data['body']], ['identity', 'main'])

    def test_postfix_updates_are_rejected(self):
        for update in ('value++','value--','++value','items[0]++'):
            with self.subTest(update=update),self.assertRaises(PixelError):
                parse_text('fn main(){var value=0;var items=[1];'+update+'}')

    def test_compound_updates_and_double_minus_string_remain_valid(self):
        parse_text('fn main(){var value=0;value+=1;value-=1;let text="--"}')
