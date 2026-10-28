"""Direct scanline drawing stays bounded and preserves the PNG contract."""
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL.Image import open as reference_decode_png

from pixellang.model import PixelError
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources
from tests.test_selfhost_png import png_data

DRIVER = '''import "png.pxl" as png
record Request { width: int, height: int, mode: string, data: [int] }
fn Draw(width: int, height: int) -> Result[png.Canvas, Error] {
 var canvas = png.NewCanvas(width, height, [16, 20, 24, 255])?
 let before = canvas
 canvas.scanlines[1] = 99
 assert(before.scanlines[1] == 16)
 canvas.scanlines[height * (width * 4 + 1) - 2] = 77
 return .Ok(canvas)
}
effect fn Run() -> Result[json, Error] {
 let request = jsonDecode::[Request](input())?
 if request.mode == "raster" {
  let image = png.DecodeRaster(request.data, 600000)?
  var samples: [int] = []
  var row = 0
  while row < image.height {
   let start = row * image.stride + 1
   samples.append(image.scanlines[start])
   samples.append(image.scanlines[start + image.width * image.channels - 1])
   row += 1
  }
  return .Ok(jsonFrom(samples))
 } else if request.mode == "decode-rgb" {
  let image = png.Decode(request.data, 600000)?
  return .Ok(jsonFrom([image.width, image.height, len(image.rgba), image.rgba[0], image.rgba[1], image.rgba[2], image.rgba[len(image.rgba) - 1]]))
 } else if request.mode == "decode" {
  let encoded = png.EncodeCanvas(Draw(request.width, request.height)?)?
  let image = png.Decode(encoded, 600000)?
  return .Ok(jsonFrom([image.width, image.height, len(image.rgba), image.rgba[0], image.rgba[len(image.rgba) - 2]]))
 } else if request.mode == "draw" {
  return .Ok(jsonFrom(png.EncodeCanvas(Draw(request.width, request.height)?)?))
 } else {
  var canvas = png.NewCanvas(request.width, request.height, [16, 20, 24, 255])?
  if request.mode == "filter" { canvas.scanlines[0] = 1 }
  if request.mode == "length" { let removed = canvas.scanlines.pop() }
  if request.mode == "byte" { canvas.scanlines[1] = 256 }
  return .Ok(jsonFrom(png.EncodeCanvas(canvas)?))
 }
}
effect fn main(){print(Run())}
'''


class PNGCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = compile_native_sources({
            'main.pxl': DRIVER,
            'png.pxl': (Path(__file__).resolve().parents[1] / 'selfhost/png.pxl').read_text(),
            'failures.pxl': (Path(__file__).resolve().parents[1] / 'selfhost/failures.pxl').read_text(),
        })

    def run_result(self, width, height, mode='draw', budget=600_000, data=()):
        vm = VM(self.compiler, input_text=json.dumps(dict(width=width, height=height, mode=mode, data=list(data))),
                max_steps=10_000_000, max_heap_items=budget)
        with patch('PIL.Image.Image.save', side_effect=AssertionError('Host PNG encoder')):
            return vm.run()[0]

    def run_canvas(self, width, height, mode='draw', budget=600_000, data=()):
        result = self.run_result(width, height, mode, budget, data)
        self.assertEqual(result['variant'], 'Ok', result)
        return json.loads(result['fields']['value'])

    def encode(self, width, height, mode='draw', budget=600_000):
        return bytes(self.run_canvas(width, height, mode, budget))

    def test_decoding_publishes_rgba_within_value_storage_budget(self):
        self.assertEqual(self.run_canvas(256, 256, "decode"), [256, 256, 262144, 99, 77])

    def test_rgb_expansion_releases_filtered_buffer_before_publication(self):
        data = png_data(256, 256, (b'\0' + bytes([10, 30, 70]) * 256) * 256, color=2)
        self.assertEqual(self.run_canvas(256, 256, 'decode-rgb', data=data),
                         [256, 256, 262144, 10, 30, 70, 255])

    def test_large_canvas_fits_one_buffer_and_small_working_space(self):
        # 256x256 RGBA is 262,144 items. Two full buffers exceed this budget;
        # compression must read the value canvas without a second full copy.
        # A host decoder independently verifies every output pixel.
        for width, height in ((1, 1), (7, 3), (256, 256)):
            with self.subTest(width=width, height=height):
                data = self.encode(width, height, budget=350_000)
                expected = bytearray([16, 20, 24, 255] * (width * height))
                expected[0] = 99
                expected[-2] = 77
                # Only the test oracle retains this entry point. Generated-code
                # gates still block PIL.Image.open throughout compiler/VM execution.
                with reference_decode_png(io.BytesIO(data)) as image:
                    self.assertEqual(image.size, (width, height))
                    self.assertEqual(image.mode, 'RGBA')
                    self.assertEqual(image.tobytes(), bytes(expected))

    def test_invalid_scanlines_are_rejected(self):
        for mode, message in (('filter', 'filter zero'), ('length', 'length mismatch'),
                              ('byte', 'byte out of range')):
            with self.subTest(mode=mode):
                result = self.run_result(2, 2, mode)
                self.assertEqual(result['variant'], 'Err', result)
                self.assertIn(message, result['fields']['error']['message'])

    def test_raster_decode_fits_single_buffer_budget(self):
        for channels, color in ((3, 2), (4, 6)):
            with self.subTest(channels=channels):
                pixel = bytes([10, 30, 70, 255][:channels])
                data = png_data(256, 256, (b'\0' + pixel * 256) * 256, color)
                self.assertEqual(self.run_canvas(256, 256, 'raster', budget=350_000, data=data),
                                 [10, pixel[-1]] * 256)

    def test_raster_unfilters_all_filters_without_losing_row_boundaries(self):
        for channels, color in ((3, 2), (4, 6)):
            width, height = 3, 5
            stride = width * channels
            raw = bytes((i * 71 + i // 3) % 256 for i in range(stride * height))
            filtered = bytearray()
            for row in range(height):
                filtered.append(row)
                for column in range(stride):
                    i = row * stride + column
                    left = raw[i - channels] if column >= channels else 0
                    above = raw[i - stride] if row else 0
                    corner = raw[i - stride - channels] if row and column >= channels else 0
                    prediction = left + above - corner
                    candidates = (left, above, corner)
                    paeth = min(candidates, key=lambda value: abs(prediction - value))
                    predictor = (0, left, above, (left + above) // 2, paeth)[row]
                    filtered.append((raw[i] - predictor) % 256)
            data = png_data(width, height, bytes(filtered), color)
            expected = [value for row in range(height)
                        for value in (raw[row * stride], raw[(row + 1) * stride - 1])]
            self.assertEqual(self.run_canvas(width, height, 'raster', data=data), expected)
