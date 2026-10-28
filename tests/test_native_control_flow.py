"""Reachability is checked by the native compiler, including edit proposals."""
import unittest
from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.vm import VM


class NativeControlFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def test_unreachable_statements_are_rejected(self):
        bodies = [
            'return; print(1)',
            'if true { return } else { return }; print(1)',
            'while true { break; print(1) }',
            'while true { continue; print(1) }',
            'match parseInt("1") { .Ok(n) => { return }, .Err(error) => { return } }; print(1)',
            'if true { match parseInt("1") { .Ok(n) => { return }, .Err(error) => { return } } } else { return }; print(1)',
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaisesRegex(PixelError, 'Unreachable statement'):
                self.compiler.request('check', files={'main.pxl': 'effect fn main() { ' + body + ' }'})

    def test_loop_exit_and_conditional_return_do_not_hide_following_code(self):
        files = {'main.pxl': '''fn choose(value: bool) -> int {
 if value { return 7 }
 while true { break }
 return 42
}
effect fn main() { print(choose(false)); print(choose(true)) }'''}
        code = self.compiler.request('compile', files=files)['result']['bytecode']
        self.assertEqual(VM(code).run(), [42, 7])

    def test_break_does_not_satisfy_function_return(self):
        with self.assertRaises(PixelError):
            self.compiler.request('check', files={'main.pxl':
                'fn missing() -> int { while true { break } }\neffect fn main() { print(missing()) }'})
