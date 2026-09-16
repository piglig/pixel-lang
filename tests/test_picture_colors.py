import unittest
from pathlib import Path

from pixellang.project import compile_project
from pixellang.vm import VM


class PictureColorTests(unittest.TestCase):
    def test_every_palette_byte_matches_independent_xor_oracle(self):
        source = Path(__file__).resolve().parents[1] / 'selfhost' / 'picture_colors.pxl'
        code = compile_project({'picture_colors.pxl': source.read_text(), 'main.pxl':
            'import "picture_colors.pxl" as c\nfn main(){print(c.Build())}'})[1].bytecode
        palette = VM(code, max_steps=1000000, max_heap_items=100000, max_heap_objects=10000).run()[0]
        categories = [16,17,18,19,20,32,48,64,80,96,112,128]
        colors = [(177,220,110),(192,171,230),(178,220,110),(230,181,127),
                  (179,220,110),(80,195,222),(240,175,94),(222,125,112),
                  (116,135,155),(154,170,220),(208,190,146),(92,218,170)]
        for category, (r,g,b) in zip(categories, colors):
            with self.subTest(category=category):
                expected = dict(category=category, red=r, green=[i ^ g for i in range(256)], blue=[i ^ b for i in range(256)])
                self.assertEqual(palette['forward'][str(category)], expected)
                self.assertEqual(palette['reverse'][str(r)], expected)
