import json
import unittest
from pathlib import Path

from tests.native_compiler import compile_native_sources
from pixellang.vm import VM


class PictureLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.code = compile_native_sources({
            'main.pxl': '''import "picture_layout.pxl" as layout
record Request { dimensions: [[int]], scale: int, rows: int }
effect fn main() {
 match jsonDecode::[Request](input()) {
  .Ok(r) => { match layout.Plan(r.dimensions, r.scale, r.rows) {
   .Ok(value) => {print(true); print(value)},
   .Err(error) => {print(false); print(error)}
  } },
  .Err(error) => {print(false); print(error)}
 }
}''',
            'failures.pxl': (root / 'selfhost/failures.pxl').read_text(),
            'picture_layout.pxl': (root / 'selfhost/picture_layout.pxl').read_text(),
        })

    def result(self, dimensions, scale=4, rows=2):
        return VM(self.code, input_text=json.dumps(dict(dimensions=dimensions, scale=scale, rows=rows)), max_steps=1000000).run()

    def plan(self, dimensions, scale=4, rows=2):
        success, result = self.result(dimensions, scale, rows)
        self.assertTrue(success, result)
        return result

    def test_wide_and_narrow_modules_fit_without_overlap(self):
        dimensions = [[2000, 1]] + [[10, 100]] * 63
        result = self.plan(dimensions)
        self.assertLess(result['width'] * result['height'], 4000000)
        self.assertGreater(8024 * (2 + sum(h * 4 + 24 for _, h in dimensions)), 32000000)
        rectangles = []
        for (w, h), (x, y) in zip(dimensions, result['origins'], strict=True):
            self.assertGreaterEqual(x, 12)
            self.assertGreaterEqual(y, 14)
            self.assertLessEqual(x + w * 4 + 12, result['width'])
            self.assertLessEqual(y + h * 4 + 12, result['height'])
            rect = (x, y, x + w * 4, y + h * 4)
            for other in rectangles:
                self.assertFalse(rect[0] < other[2] and rect[2] > other[0] and rect[1] < other[3] and rect[3] > other[1])
            rectangles.append(rect)
        self.assertEqual(self.plan(dimensions), result)

    def test_keeps_vertical_layout_when_smaller(self):
        dimensions = [[100, 100]] * 32
        result = self.plan(dimensions)
        self.assertLessEqual(result['width'] * result['height'], 424 * (2 + 32 * 424))

    def test_limits_are_still_enforced(self):
        for dimensions, scale, rows in (([], 4, 2), ([[1, 1]] * 129, 4, 2),
                                         ([[1001, 1000]], 4, 2), ([[1, 1]], 3, 2),
                                         ([[1, 1]], 4, 0), ([[1000, 1000]], 24, 2),
                                         ([[9223372036854775807, 1]], 4, 2)):
            with self.subTest(dimensions=dimensions[:2]):
                success, error = self.result(dimensions, scale, rows)
                self.assertFalse(success)
                self.assertEqual(error['kind'], 'compiler')
                self.assertEqual(error['code'], 'compiler.invalid_data')
                self.assertTrue(error['message'])
