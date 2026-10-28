from tests.native_compiler import native_driver
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pixellang.vm import VM
from pixellang.code_image import CodeImage
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS, SELFHOST_MAX_OUTPUT_CHARS

ROOT = Path(__file__).resolve().parents[1]


class SelfHostedParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / "selfhost").glob("*.pxl")}
        from tests.native_compiler import load_native_compiler
        cls.bytecode = load_native_compiler(driver=None)
        cls.image = CodeImage(cls.bytecode)

    def parse(self, source, name="test.pxl"):
        vm = VM(self.image, **SELFHOST_HEAP_LIMITS, input_text=json.dumps({
            "source": name, "text": source, "operation": "parse"
        }), max_steps=40_000_000, max_depth=1024, max_output_chars=SELFHOST_MAX_OUTPUT_CHARS)
        with patch("pixellang.text.parse_text", side_effect=AssertionError("delegated")), patch(
            "pixellang.text.lex", side_effect=AssertionError("delegated")
        ):
            return vm.run()[0]

    def test_only_symbolic_boolean_operators_are_syntax(self):
        for expression in ['true and false', 'true or false', 'not true']:
            with self.subTest(expression=expression):
                parsed = self.parse('fn main(){let value=' + expression + '}')
                self.assertTrue(parsed['diagnostics'])
        parsed = self.parse('fn main(){let and=true; let or=false; let not=3; print(!or && and); print(not)}')
        self.assertEqual(parsed['diagnostics'], [])

    def test_parses_every_own_source_and_example(self):
        files = dict(self.files)
        files.update({str(p.relative_to(ROOT)): p.read_text() for p in (ROOT / "examples").rglob("*.pxl")})
        with native_driver(self.image) as run:
            for name, source in files.items():
                with self.subTest(source=name):
                    result = run(dict(operation='parse',source=name,text=source),steps=40_000_000,output_chars=SELFHOST_MAX_OUTPUT_CHARS)[0]
                    self.assertEqual(result["diagnostics"], [])
                    nodes = result["nodes"]
                    self.assertTrue(result["roots"])
                    for index, node in enumerate(nodes):
                        self.assertLessEqual(0, node["start"])
                        self.assertLessEqual(node["start"], node["end"])
                        self.assertLessEqual(node["end"], len(source))
                        for child in node["children"]:
                            self.assertLess(child, index)
                            self.assertGreaterEqual(child, 0)
                            self.assertLessEqual(node["start"], nodes[child]["start"])
                            self.assertGreaterEqual(node["end"], nodes[child]["end"])

    def test_precedence_and_associativity(self):
        result = self.parse("let value = 10 - 3 - 2 * 4")
        self.assertEqual(result["diagnostics"], [])
        nodes = result["nodes"]
        expr = nodes[nodes[result["roots"][0]]["children"][0]]
        self.assertEqual((expr["kind"], expr["text"]), ("binary", "-"))
        self.assertEqual(nodes[expr["children"][0]]["text"], "-")
        self.assertEqual(nodes[expr["children"][1]]["text"], "*")

    def test_generic_declarations_and_match_arms(self):
        result = self.parse("""enum Choice[T] { Value(item: T), Empty }
record Box[T] { value: T }
fn unwrap[T](x: Choice[T], fallback: T) -> T {
 return match x { Choice.Value(value) => value, _ => fallback }
}
""")
        self.assertEqual(result["diagnostics"], [])
        kinds = [node["kind"] for node in result["nodes"]]
        for kind in ("enum", "record", "type_parameter", "variant_decl", "match", "arm"):
            self.assertIn(kind, kinds)

    def test_syntax_errors_point_to_offending_token(self):
        for source, token in [
            ("let x = 1 let y = 2", "let y"),
            ("fn main( {", "{"),
            ('let x = match value { Option.Some(x) x }', "x }"),
            ("record R { first: int second: int }", "second"),
        ]:
            with self.subTest(source=source):
                errors = self.parse(source, "bad.pxl")["diagnostics"]
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0]["phase"], "parse")
                self.assertEqual(errors[0]["source"], "bad.pxl")
                self.assertEqual(errors[0]["start"], source.index(token))
                self.assertEqual(errors[0]["column"], source.index(token) + 1)

    def test_result_and_expression_body_spans(self):
        source = "fn count() -> int = 12 + 3"
        result = self.parse(source)
        self.assertEqual(result["diagnostics"], [])
        spans = {
            node["kind"]: source[node["start"]:node["end"]]
            for node in result["nodes"]
            if node["kind"] in ("result", "expression_body")
        }
        self.assertEqual(spans, {"result": "int", "expression_body": "12 + 3"})

    def test_specialization_index_and_map_are_distinct(self):
        source = '''fn identity[T](value: T) -> T = value
fn main() {
 let values = [1, 2]
 let first = values[0]
 let copy = identity::[[int]](values)
 let decode = jsonDecode::[map[string, [int]]]("{}")
 let mapper = identity::[fn(int)->int](fn(x: int) = x)
 let entries = map[string, int]{"key" + "suffix": first}
}
'''
        result = self.parse(source)
        self.assertEqual(result["diagnostics"], [])
        nodes = result["nodes"]
        indexes = [n for n in nodes if n["kind"] == "index"]
        self.assertEqual(len(indexes), 1)
        self.assertEqual(source[indexes[0]["start"]:indexes[0]["end"]], "values[0]")
        specializations = [n for n in nodes if n["kind"] == "specialize"]
        self.assertEqual(len(specializations), 3)
        self.assertEqual(
            [nodes[n["children"][1]]["kind"] for n in specializations],
            ["type_array", "type", "type_fn"],
        )
        entry = next(n for n in nodes if n["kind"] == "map_entry")
        self.assertEqual(nodes[entry["children"][0]]["kind"], "binary")

    def test_qualified_generic_variant_pattern(self):
        result = self.parse('''import "other.pxl" as other
fn extract(value: other.Choice[int]) -> int {
 return match value { other.Choice[int].Value(item) => item, _ => 0 }
}
''')
        self.assertEqual(result["diagnostics"], [])
        pattern = next(n for n in result["nodes"] if n["kind"] == "variant_pattern")
        self.assertEqual(pattern["text"], "Value")
        qualifier = result["nodes"][pattern["children"][0]]
        self.assertEqual(qualifier["kind"], "pattern_qualifier")
        typ = result["nodes"][qualifier["children"][0]]
        self.assertEqual(typ["text"], "other.Choice")
        self.assertEqual(result["nodes"][typ["children"][0]]["text"], "int")

    def test_index_requires_exactly_one_expression(self):
        for source in ("let x = values[]", "let x = values[1, 2]"):
            with self.subTest(source=source):
                self.assertTrue(self.parse(source)["diagnostics"])

    def test_nesting_limits_report_diagnostics(self):
        for source in (
            "let value: " + "[" * 140 + "int" + "]" * 140 + " = []",
            "fn main() {" + "if true {" * 140 + "}" * 141,
            "let value = " + "(" * 140 + "1" + ")" * 140,
        ):
            with self.subTest(prefix=source[:30]):
                errors = self.parse(source)["diagnostics"]
                self.assertEqual(len(errors), 1)
                self.assertIn("nesting exceeds", errors[0]["message"])

    def test_export_rejects_non_declarations(self):
        for source in ("export let x = 1", "export export fn main() {}", "export return 1"):
            self.assertEqual(self.parse(source)["diagnostics"][0]["start"], 7)

    def test_module_call_inside_an_index_is_not_a_type_argument(self):
        source = 'import "lib.pxl" as lib\nfn main() { let values = [1]; print(values[lib.Index()]); print(values[lib.offset]) }'
        result = self.parse(source)
        self.assertEqual(result['diagnostics'], [])
        indexes = [n for n in result['nodes'] if n['kind'] == 'index']
        self.assertEqual(len(indexes), 2)
        self.assertEqual(result['nodes'][indexes[0]['children'][1]]['kind'], 'call')
        self.assertEqual(result['nodes'][indexes[1]['children'][1]]['kind'], 'field')

    def test_native_parser_matches_reference_vm(self):
        with native_driver(self.image) as run:
            for source in ('fn main(){let f=fn(x:int)=x+1;let y=f(2)}', 'fn main(){let = }'):
                with self.subTest(source=source):
                    self.assertEqual(run(dict(operation='parse',source='test.pxl',text=source),
                        steps=40_000_000,output_chars=SELFHOST_MAX_OUTPUT_CHARS)[0],self.parse(source))

    def test_postfix_increment_and_decrement_are_removed(self):
        for update in ('value++','value--','++value','items[0]++'):
            with self.subTest(update=update):
                result=self.parse('fn main(){var value=0;var items=[1];'+update+'}')
                self.assertTrue(result['diagnostics'])
                self.assertFalse(any(node['kind']=='update' for node in result['nodes']))
        result=self.parse('fn main(){var value=0;var items=[1];value+=1;value-=1;items[0]+=2;let text="--"}')
        self.assertEqual(result['diagnostics'],[])
        self.assertEqual([node['text'] for node in result['nodes'] if node['kind']=='assign'],['+=','-=','+='])


    def test_retired_exception_syntax_is_rejected(self):
        for source in ('fn main(){try {let x=1} catch error {let y=2}}',
                       'fn main(){catch error {let x=1}}'):
            with self.subTest(source=source):
                parsed = self.parse(source)
                self.assertTrue(parsed['diagnostics'])
                self.assertFalse(any(node['kind'] == 'try' for node in parsed['nodes']))


    def test_retired_keywords_are_ordinary_user_names(self):
        parsed = self.parse('fn fail(value:int)->int=value\nfn main(){let try=fail(1);let catch=try+1}')
        self.assertEqual(parsed['diagnostics'], [])
