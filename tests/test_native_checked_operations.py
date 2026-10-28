"""Native type checking and lowering for Result-returning data operations."""
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from pixellang.fileaccess import FileAccess
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources, native_driver
from tests.selfhost_runtime import SELFHOST_MAX_OUTPUT_CHARS

ROOT=Path(__file__).resolve().parents[1]
DRIVER='''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "instances.pxl" as instances
import "closures.pxl" as closures
import "ir.pxl" as ir
import "assembler.pxl" as assembler
effect fn main(){
 match jsonDecode::[map[string,string]](input()) {
  .Ok(files)=>{Compile(files)},
  .Err(error)=>{print(error)}
 }
}
effect fn Compile(files:map[string,string]){
 let checked=checker.Check(types.Analyze(bindings.Bind(project.Build(files,"main.pxl"))))
 var planned=closures.Build(instances.Build(checked))
 print(jsonFrom(assembler.Assemble(ir.Build(&planned))))
}'''


def sources():
    files={'main.pxl':DRIVER}
    pending=re.findall(r'import "([^"]+)"',DRIVER)
    while pending:
        name=pending.pop()
        if name in files:continue
        files[name]=(ROOT/'selfhost'/name).read_text()
        pending.extend(re.findall(r'import "([^"]+)"',files[name]))
    return files


class NativeCheckedOperationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code=compile_native_sources(sources())
        cls.run_compiler=staticmethod(cls.enterClassContext(native_driver(cls.code)))

    def compile(self,source,**modules):
        with patch('pixellang.text.parse_text',side_effect=AssertionError('host parser is forbidden')):
            output=self.run_compiler({'main.pxl':source,**modules},steps=40_000_000,
                                     output_chars=SELFHOST_MAX_OUTPUT_CHARS)
        return json.loads(output[0])

    def execute(self,source,*,files=None,**kwargs):
        result=self.compile(source,**(files or {}))
        self.assertEqual(result['diagnostics'],[],result['diagnostics'])
        return VM(result['bytecode'],**kwargs).run(),result['bytecode']

    def test_numeric_parsing_and_propagation(self):
        output,code=self.execute('''fn next(text:string)->Result[int,Error]=.Ok(parseInt(text)?+1)
effect fn main(){
 print(next("41")==Result::[int,Error].Ok(42))
 match next("bad") {.Ok(value)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
 match parseFloat64("1.25") {.Ok(value)=>{print(value)},.Err(error)=>{print(error.code)}}
}''')
        self.assertEqual(output,[True,'numeric.invalid_text',1.25])
        instructions=[i for f in code['functions'].values() for i in f['instructions']]
        self.assertTrue(any(i['op']=='RESULT_BUILTIN' for i in instructions))
        self.assertFalse(any(i['op'] in ('TRY','END_TRY') for i in instructions))

    def test_generic_typed_decode_and_json_navigation(self):
        output,code=self.execute('''record Row {amount:int}
fn decode[T](text:string)->Result[T,Error]=jsonDecode::[T](text)
fn amount(text:string)->Result[int,Error]{
 let object=jsonParse(text)?
 return jsonAsInt(jsonGet(object,"amount")?)
}
effect fn main(){
 match decode::[Row]("{\\"amount\\":7}") {.Ok(row)=>{print(row.amount)},.Err(error)=>{print(error.code)}}
 print(amount("{\\"amount\\":9}")==Result::[int,Error].Ok(9))
 match amount("{}") {.Ok(value)=>{print(value)},.Err(error)=>{print(error.code)}}
 match decode::[Row]("{\\"amount\\":false}") {.Ok(row)=>{print(row.amount)},.Err(error)=>{print(error.path)}}
}''')
        self.assertEqual(output,[7,True,'json.missing_key','$.amount'])
        self.assertTrue(any(i['op']=='RESULT_DECODE' for f in code['functions'].values() for i in f['instructions']))

    def test_io_propagates_without_language_handlers(self):
        source='''effect fn load(path:string)->Result[[int],Error]=jsonDecode::[[int]](readText(path)?)
effect fn main(){
 match load("numbers.json") {.Ok(values)=>{print(values)},.Err(error)=>{print(error.code)}}
 match load("absent.json") {.Ok(values)=>{print(values)},.Err(error)=>{print(error.code)}}
}'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'numbers.json').write_text('[2,3]')
            output,_=self.execute(source,file_access=FileAccess(root))
            self.assertEqual(output,[[2,3],'io.not_found'])

    def test_utf8_and_typed_decompression_use_results(self):
        output,_=self.execute('''fn text(bytes:[int])->Result[string,Error]=utf8Decode(bytes)
fn roundtrip()->Result[bool,Error]{
 let packed=zlibCompress([1,2,3])?
 return .Ok(zlibDecompress::[[int]](packed,100)==Result::[[int],Error].Ok([1,2,3]))
}
effect fn main(){
 print(text([65,66])==Result::[string,Error].Ok("AB"))
 match text([255]) {.Ok(value)=>{print("unexpected")},.Err(error)=>{print(true)}}
 print(roundtrip()==Result::[bool,Error].Ok(true))
}''')
        self.assertEqual(output,[True,True,True])

    def test_standard_json_wrappers_propagate_typed_errors(self):
        output,_=self.execute('''import "std/json.pxl" as json
fn read(text:string)->Result[int,Error] {
 let value=json.Parse(text)?
 return json.Int(json.Get(value,"n")?)
}
effect fn main(){
 print(read("{\\"n\\":4}")==Result::[int,Error].Ok(4))
 match read("{}") {.Ok(n)=>{print(n)},.Err(error)=>{print(error.code)}}
}''',files={'std/json.pxl':(ROOT/'pixellang/stdlib/json.pxl').read_text()})
        self.assertEqual(output,[True,'json.missing_key'])

    def test_standard_line_reader_counts_and_preserves_state_on_failure(self):
        source='''import "std/io.pxl" as io
effect fn show(number:int,text:string){print(number);print(text)}
effect fn main(){
 match io.ForEachLine("lines.txt",show) {.Ok(count)=>{print(count)},.Err(error)=>{print(error.code)}}
 var reader=io.Lines("missing.txt")
 let before=reader
 match io.NextLine(&reader) {.Ok(line)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
 print(reader==before)
}'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'lines.txt').write_text('one\r\n猫\rthree\n')
            output,_=self.execute(source,files={'std/io.pxl':(ROOT/'pixellang/stdlib/io.pxl').read_text()},file_access=FileAccess(root))
            self.assertEqual(output,[1,'one',2,'猫',3,'three',4,'',4,'io.not_found',True])

    def test_standard_csv_paths_cli_and_empty_statistics_use_values(self):
        source='''import "std/csv.pxl" as csv
import "std/path.pxl" as path
import "std/cli.pxl" as cli
import "std/stats.pxl" as stats
fn roundTrip(text:string)->Result[[[string]],Error] = csv.Parse(csv.Encode(csv.Parse(text)?)?)
effect fn main(){
 print(roundTrip("a,b\\n猫,d\\n")==Result::[[[string]],Error].Ok([["a","b"],["猫","d"]]))
 match csv.Parse("a\\"b") {.Ok(rows)=>{print("unexpected")},.Err(error)=>{print(error.code);print(error.path)}}
 print(path.Clean("a/../b")==Result::[string,Error].Ok("b"))
 match path.Base("../x") {.Ok(name)=>{print(name)},.Err(error)=>{print(error.code)}}
 match cli.Parse(["--verbose","file"],["verbose"],[]) {.Ok(args)=>{print(args.options["verbose"]);print(args.files)},.Err(error)=>{print(error.code)}}
 match cli.Parse(["--bad"],[],[]) {.Ok(args)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
 print(stats.Summary([])==Option::[map[string,int]].None)
 match stats.Summary([2,4]) {Option.Some(summary)=>{print(summary["mean"])},Option.None=>{print("unexpected")}}
}'''
        files={'std/'+name:(ROOT/'pixellang/stdlib'/name).read_text() for name in ('csv.pxl','path.pxl','cli.pxl','stats.pxl','error.pxl')}
        output,_=self.execute(source,files=files)
        self.assertEqual(output,[True,'csv.unexpected_quote','1',True,'path.escapes_root','true',['file'],'cli.unknown_option',True,3])

    def test_line_reader_keeps_state_after_later_chunk_failure(self):
        source='''import "std/io.pxl" as io
effect fn main(){
 var reader=io.Lines("broken.txt")
 let before=reader
 match io.NextLine(&reader) {.Ok(line)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
 print(reader==before)
}'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'broken.txt').write_bytes(b'a'*65536+b'\xff')
            output,_=self.execute(source,files={'std/io.pxl':(ROOT/'pixellang/stdlib/io.pxl').read_text()},file_access=FileAccess(root))
            self.assertEqual(output,['io.invalid_encoding',True])

    def test_line_limit_excludes_split_crlf_and_rejects_oversized_line_atomically(self):
        source='''import "std/io.pxl" as io
effect fn length(reader:inout io.LineReader)->Result[int,Error]{
 match io.NextLine(&reader)? {Option.Some(line)=>{return .Ok(len(line.text))},Option.None=>{return .Ok(-1)}}
}
effect fn main(){
 var reader=io.Lines("boundary.txt")
 print(length(&reader)==Result::[int,Error].Ok(65534))
 print(length(&reader)==Result::[int,Error].Ok(262144))
 print(length(&reader)==Result::[int,Error].Ok(0))
 print(length(&reader)==Result::[int,Error].Ok(-1))
 var tooLong=io.Lines("large.txt")
 let before=tooLong
 match io.NextLine(&tooLong) {.Ok(line)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
 print(tooLong==before)
}'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'boundary.txt').write_bytes(b'x'*65534+b'\n'+b'a'*262144+b'\r\n')
            (root/'large.txt').write_bytes(b'a'*262145+b'\n')
            output,_=self.execute(source,files={'std/io.pxl':(ROOT/'pixellang/stdlib/io.pxl').read_text()},file_access=FileAccess(root))
            self.assertEqual(output,[True,True,True,True,'io.line_too_long',True])

    def test_all_standard_modules_typecheck_with_result_operations(self):
        files={'std/'+p.name:p.read_text() for p in (ROOT/'pixellang/stdlib').glob('*.pxl')}
        source='\n'.join('import "'+name+'" as lib'+str(index) for index,name in enumerate(sorted(files)))+'\nfn main(){}'
        self.assertEqual(self.compile(source,**files)['diagnostics'],[])

    def test_time_range_errors_and_logical_step_boundaries(self):
        source='''import "std/time.pxl" as time
fn step(index:int,millis:int)->Result[time.Step,Error]=time.At(index,time.Milliseconds(millis)?)
effect fn main(){
 print(time.Seconds(9223372036)==Result::[time.Duration,Error].Ok(time.Nanoseconds(9223372036000000000)))
 print(time.Seconds(-9223372036)==Result::[time.Duration,Error].Ok(time.Nanoseconds(-9223372036000000000)))
 match time.Seconds(9223372037) {.Ok(value)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
 match time.Seconds(-9223372037) {.Ok(value)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
 match step(0,100) {.Ok(value)=>{print(value.elapsed.nanoseconds);print(value.delta.nanoseconds)},.Err(error)=>{print(error.code)}}
 match step(2,100) {.Ok(value)=>{print(value.elapsed.nanoseconds);print(value.delta.nanoseconds)},.Err(error)=>{print(error.code)}}
 match step(-1,100) {.Ok(value)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
 match step(1,0) {.Ok(value)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
 match time.At(2,time.Nanoseconds(9223372036854775807)) {.Ok(value)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
}'''
        files={'std/'+name:(ROOT/'pixellang/stdlib'/name).read_text() for name in ('time.pxl','error.pxl')}
        output,_=self.execute(source,files=files)
        self.assertEqual(output,[True,True,'time.duration_range','time.duration_range',0,0,200000000,100000000,'time.invalid_index','time.invalid_interval','time.elapsed_range'])

    def test_repeat_reports_negative_counts_and_preserves_value_isolation(self):
        source='''import "std/collections.pxl" as collections
fn twice[T](value:T)->Result[[T],Error]=collections.Repeat(value,2)
effect fn main(){
 let original=[1]
 match twice(original) {
  .Ok(values)=>{var changed=values;changed[0][0]=9;print(changed);print(values);print(original)},
  .Err(error)=>{print(error.code)}
 }
 print(collections.Repeat(1,0)==Result::[[int],Error].Ok([]))
 match collections.Repeat(1,-1) {.Ok(values)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
}'''
        files={'std/'+name:(ROOT/'pixellang/stdlib'/name).read_text() for name in ('collections.pxl','error.pxl')}
        output,_=self.execute(source,files=files)
        self.assertEqual(output,[[[9],[1]],[[1],[1]],[1],True,'collection.negative_count'])

    def test_selfhost_lexer_uses_results_for_float_and_string_validation(self):
        driver='''import "lexer.pxl" as lexer
record Request {source:string,text:string}
effect fn main(){
 match jsonDecode::[Request](input()) {
  .Ok(request)=>{print(lexer.Scan(request.source,request.text))},
  .Err(error)=>{print(error.code)}
 }
}'''
        files={name:(ROOT/'selfhost'/name).read_text() for name in ('model.pxl','lexer.pxl')}
        result=self.compile(driver,**files)
        self.assertEqual(result['diagnostics'],[])
        code=result['bytecode']
        instructions=[i for f in code['functions'].values() for i in f['instructions']]
        self.assertFalse(any(i['op'] in ('TRY','END_TRY') for i in instructions))
        self.assertTrue(any(i['op']=='RESULT_BUILTIN' and i['arg']['name']=='parseFloat64' for i in instructions))
        self.assertTrue(any(i['op']=='RESULT_DECODE' for i in instructions))
        cases=[('let 数量 = 1_234\n2.5e-1; "\\u0041"',None),
               ('1e999','Float64 literal exceeds finite range'),
               ('"\\q"','Invalid string escape or Unicode scalar'),
               ('"\\uD800"','Invalid string escape or Unicode scalar')]
        with patch('pixellang.text.lex',side_effect=AssertionError('host lexer is forbidden')):
            for text,message in cases:
                scanned=VM(code,input_text=json.dumps(dict(source='test.pxl',text=text)),max_steps=5_000_000).run()[0]
                if message is None:
                    self.assertEqual(scanned['diagnostics'],[])
                    self.assertEqual([t['value'] for t in scanned['tokens']],['let','数量','=','1234','\n','2.5e-1',';','A','<eof>'])
                    self.assertEqual((scanned['tokens'][1]['start'],scanned['tokens'][1]['end']),(4,6))
                else:
                    self.assertEqual(len(scanned['diagnostics']),1)
                    error=scanned['diagnostics'][0]
                    self.assertEqual((error['message'],error['start'],error['line'],error['column']),(message,0,1,1))

    def test_compile_time_arithmetic_reports_failures_as_values(self):
        source='''import "constant_math.pxl" as arithmetic
fn comparison()->Result[bool,Error]=jsonAsBool(arithmetic.Binary("<","int",jsonFrom(1),jsonFrom(2))?)
effect fn integer(op:string,left:int,right:int){
 match arithmetic.Integer(op,left,right) {.Ok(value)=>{print(value)},.Err(error)=>{print(error.message)}}
}
effect fn floating(op:string,left:float64,right:float64){
 match arithmetic.Float(op,left,right) {.Ok(value)=>{print(value)},.Err(error)=>{print(error.message)}}
}
effect fn main(){
 integer("+",40,2)
 integer("+",9223372036854775807,1)
 integer("-",-9223372036854775808,1)
 integer("*",-9223372036854775808,-1)
 integer("/",-9223372036854775808,-1)
 integer("/",1,0)
 integer("%",-9223372036854775808,-1)
 floating("+",1.25,2.5)
 floating("*",1.7976931348623157e308,2.0)
 floating("/",1.0,0.0)
 print(comparison()==Result::[bool,Error].Ok(true))
 match arithmetic.Binary("+","int",jsonFrom("bad"),jsonFrom(2)) {.Ok(value)=>{print("unexpected")},.Err(error)=>{print(error.code)}}
}'''
        files={name:(ROOT/'selfhost'/name).read_text() for name in ('constant_math.pxl','failures.pxl')}
        output,code=self.execute(source,files=files)
        self.assertEqual(output,[42,*(['Integer overflow in state initializer']*4),'Division by zero in state initializer',0,3.75,'Float64 overflow in state initializer','Division by zero in state initializer',True,'json.type_mismatch'])
        self.assertFalse(any(i['op'] in ('TRY','END_TRY') for f in code['functions'].values() for i in f['instructions']))

    def test_raw_return_discard_and_pure_io_are_rejected(self):
        for source,message in [
            ('fn f()->int=parseInt("1")\nfn main(){}','Type mismatch'),
            ('fn main(){parseInt("1")}','Must-use value cannot be discarded'),
            ('fn f()->Result[string,Error]=readText("x")\nfn main(){}','effect'),
            ('fn f()->Result[int,string]=.Ok(parseInt("1")?)\nfn main(){}','same error type'),
        ]:
            with self.subTest(source=source):
                result=self.compile(source)
                self.assertTrue(result['diagnostics'])
                self.assertIn(message,str(result['diagnostics']))


if __name__=='__main__':unittest.main()
