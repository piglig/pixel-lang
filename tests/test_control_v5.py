import unittest

from pixellang.model import PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project, project_text
from pixellang.vm import VM


class ControlV5Tests(unittest.TestCase):
    def run_source(self, source):
        image, result = compile_project({"main.pxl": source})
        expected = VM(result.bytecode).run()
        recovered = project_text(decode_picture(encode_picture(image)))
        self.assertEqual(
            VM(compile_project(recovered["files"])[1].bytecode).run(), expected
        )
        return expected

    def test_continue_advances_collection_and_break_targets_nearest_loop(self):
        self.assertEqual(
            self.run_source("""fn main() {
 var total = 0
 for n in [1, 2, 3, 4] {
  if n == 2 { continue }
  if n == 4 { break }
  var i = 0
  while i < 4 {
   i += 1
   if i == 2 { continue }
   if i == 3 { break }
   total += n
  }
 }
 print(total)
}"""),
            [4],
        )

    def test_transfer_unwinds_only_handlers_inside_loop(self):
        for transfer in ("break", "continue"):
            with self.subTest(transfer=transfer):
                self.assertEqual(
                    self.run_source(
                        """fn main() {
 try {
  for n in [1, 2] {
   try { try { TRANSFER } catch inner { print("stale inner") } }
   catch outer { print("stale outer") }
  }
  fail("after loop")
 } catch expected { print("outer survived") }
}""".replace("TRANSFER", transfer)
                    ),
                    ["outer survived"],
                )

    def test_transfer_from_catch_does_not_pop_surrounding_handler(self):
        self.assertEqual(
            self.run_source("""fn main() {
 try {
  for n in [1, 2] {
   try { fail("inside") } catch caught { continue }
  }
  fail("outside")
 } catch expected { print("caught") }
}"""),
            ["caught"],
        )

    def test_inner_loop_preserves_handler_inside_outer_loop(self):
        self.assertEqual(
            self.run_source("""fn main() {
 for n in [1, 2] {
  try {
   while true { break }
   fail("caught per iteration")
  } catch expected { print(n) }
 }
}"""),
            [1, 2],
        )

    def test_rejects_transfer_outside_loop_and_missing_return(self):
        for source in (
            "fn main() { break }",
            "fn main() { try { continue } catch e {} }",
            "fn value() -> int { while true { break } }\nfn main() {}",
            "fn main() { while true { break; print(1) } }",
        ):
            with self.subTest(source=source), self.assertRaises(PixelError):
                compile_project({"main.pxl": source})
