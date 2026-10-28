"""The language checker uses native constructor coverage, including generics."""
import hashlib
import json
from pathlib import Path
import re
import unittest

from pixellang.vm import VM
from tests.native_compiler import compile_native_sources

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
effect fn main() {
 match jsonDecode::[map[string,string]](input()) { .Ok(files)=>{Dispatch(files)}, .Err(error)=>{print(error.message)} }
}
effect fn Dispatch(files:map[string,string]) {
 let checked=checker.Check(types.Analyze(bindings.Bind(project.Build(files,"main.pxl"))))
 print(checked.diagnostics)
}'''


class NativeMatchCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {'main.pxl': DRIVER}
        pending = re.findall(r'import "([^"]+)"', DRIVER)
        while pending:
            name = pending.pop()
            if name in files:
                continue
            files[name] = (ROOT/'selfhost'/name).read_text()
            pending.extend(re.findall(r'import "([^"]+)"', files[name]))
        cls.code = compile_native_sources(files)
        encoded = json.dumps(cls.code, sort_keys=True, separators=(',', ':')).encode()
        Path('/tmp/pixel-match-coverage-driver.json').write_bytes(encoded)
        Path('/tmp/pixel-match-coverage-provenance.json').write_text(json.dumps(dict(
            sha256=hashlib.sha256(encoded).hexdigest(),
            sourceSha256=hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest())))

    def check(self, source, **modules):
        return VM(self.code, input_text=json.dumps({'main.pxl': source, **modules}),
                  max_steps=40_000_000).run()[0]

    def test_exhaustive_expression_and_statement_return_paths(self):
        diagnostics = self.check('''enum State {A,B(value:int)}
fn expression(s:State)->int = match s {.A=>0,.B(value)=>value}
fn statement(s:State)->int {
 match s {.A=>{return 0},.B(value)=>{return value}}
}
fn main(){}''')
        self.assertEqual(diagnostics, [])

    def test_missing_variant_has_a_specific_diagnostic(self):
        diagnostics = self.check('enum State {A,B}\nfn f(s:State)->int=match s {.A=>0}\nfn main(){}')
        self.assertTrue(any(d['message'] == 'Missing match arm: B' for d in diagnostics), diagnostics)

    def test_duplicate_and_fully_covered_fallback_are_unreachable(self):
        for arms in ['.A=>0,.A=>1,.B=>2', '_=>0,.A=>1', '.A=>0,.B=>1,_=>2']:
            with self.subTest(arms=arms):
                diagnostics = self.check('enum State {A,B}\nfn f(s:State)->int=match s {'+arms+'}\nfn main(){}')
                self.assertTrue(any('unreachable match arm' in d['message'] for d in diagnostics), diagnostics)

    def test_generic_enum_and_option_payloads_use_concrete_domains(self):
        self.assertEqual(self.check('''enum Box[T] {Empty,Value(value:T)}
fn unwrap[T](box:Box[T], fallback:T)->T = match box {.Empty=>fallback,.Value(value)=>value}
fn optional(value:Option[int])->int = match value {Option.None=>0,Option.Some(n)=>n}
effect fn main(){print(unwrap(Box::[int].Value(3),0));print(optional(Option.Some(2)))}'''), [])

    def test_products_nested_payloads_and_guard_scope(self):
        self.assertEqual(self.check("""enum S {A,B(value:Option[int])}
enum E {Go,Stop}
fn f(s:S,e:E)->int=match (s,e) {
 (.A,_)=>0,
 (.B(.Some(n)),.Go) if n>0=>n,
 (.B(.Some(n)),.Go)=>-n,
 (.B(.None),.Go)=>1,
 (.B(_),.Stop)=>2
}
fn main(){}"""), [])

    def test_partial_nested_and_guarded_coverage_do_not_prove_exhaustiveness(self):
        for arms in [
            '(.A,_)=>0, (.B(.Some(n)),_)=>n',
            '(.A,_)=>0, (.B(_),_) if true=>1',
            '(.A,.Go)=>0, (.B(_),.Stop)=>1',
        ]:
            diagnostics=self.check('enum S {A,B(value:Option[int])}\nenum E {Go,Stop}\nfn f(s:S,e:E)->int=match (s,e) {'+arms+'}\nfn main(){}')
            self.assertTrue(any('Non-exhaustive' in d['message'] for d in diagnostics), diagnostics)

    def test_pattern_arity_duplicate_bindings_and_guard_types(self):
        for arms, message in [
            ('(.A)=>0', 'Pattern count'),
            ('(x,x)=>0', 'Duplicate local name'),
            ('(_,_) if 1=>0,(_,_)=>1', 'Type mismatch'),
            ('(.A,.Go)=>0, (.A,.Go)=>1,(_,_)=>2', 'unreachable match arm'),
        ]:
            diagnostics=self.check('enum S {A,B}\nenum E {Go,Stop}\nfn f(s:S,e:E)->int=match (s,e) {'+arms+'}\nfn main(){}')
            self.assertTrue(any(message in d['message'] for d in diagnostics), diagnostics)

    def test_payload_qualifier_and_non_enum_errors_are_not_hidden(self):
        sources = [
            'enum S {A(value:int)}\nfn f(s:S)->int=match s {.A=>0}',
            'enum S {A}\nenum T {A}\nfn f(s:S)->int=match s {T.A=>0}',
            'fn f(s:int)->int=match s {.A=>0}',
        ]
        for source in sources:
            with self.subTest(source=source):
                self.assertTrue(self.check(source+'\nfn main(){}'))


if __name__ == '__main__':
    unittest.main()
