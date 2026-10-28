import unittest

from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM


class ControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def request(self, operation, **args):
        response = self.compiler.request(operation, **args)
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def run_source(self, source):
        files = {"main.pxl": source}
        expected = VM(self.request('compile', files=files)['bytecode']).run()
        image = self.request('export-png', files=files)['image']
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)
        return expected

    def test_continue_advances_collection_and_break_targets_nearest_loop(self):
        self.assertEqual(
            self.run_source("""effect fn main() {
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

    def test_transfer_leaves_nested_match_blocks_inside_loop(self):
        for transfer in ("break", "continue"):
            with self.subTest(transfer=transfer):
                self.assertEqual(self.run_source("""effect fn main() {
 for n in [1, 2] {
  match parseInt("1") {
   .Ok(value) => {
    match parseInt("2") { .Ok(inner) => { TRANSFER }, .Err(error) => { print("unexpected inner") } }
   },
   .Err(error) => { print("unexpected outer") }
  }
  print("stale continuation")
 }
 print("outer survived")
}""".replace("TRANSFER", transfer)), ["outer survived"])

    def test_continue_from_error_arm_preserves_surrounding_loop(self):
        self.assertEqual(self.run_source("""effect fn main() {
 for n in [1, 2] {
  match parseInt("invalid") { .Ok(value) => { print("unexpected") }, .Err(error) => { continue } }
  print("stale continuation")
 }
 print("caught")
}"""), ["caught"])

    def test_inner_loop_break_preserves_outer_match_arm(self):
        self.assertEqual(self.run_source("""effect fn main() {
 for n in [1, 2] {
  match parseInt("invalid") {
   .Ok(value) => { print("unexpected") },
   .Err(error) => { while true { break }; print(n) }
  }
 }
}"""), [1, 2])

    def test_rejects_transfer_outside_loop_and_missing_return(self):
        for source in (
            "effect fn main() { break }",
            "effect fn main() { match parseInt(\"bad\") { .Ok(value) => {}, .Err(error) => { continue } } }",
            "fn value() -> int { while true { break } }\neffect fn main() {}",
            "effect fn main() { while true { break; print(1) } }",
        ):
            with self.subTest(source=source), self.assertRaises(PixelError):
                self.request("compile", files={"main.pxl": source})
