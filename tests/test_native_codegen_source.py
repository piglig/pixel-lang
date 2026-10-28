"""Generated PixelLang programs execute as standalone native binaries."""
from tests.native_compiler import native_service
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

from pixellang.native_codegen import build_native
from pixellang.vm import VM
from pixellang.fileaccess import FileAccess


@unittest.skipUnless(shutil.which('clang') or shutil.which('cc'), 'C compiler required')
class NativeCodegenSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)

    def test_nested_array_calls_slice_and_value_equality(self):
        source = '''fn first(values: [[string]]) -> [string] = values[0]
effect fn main() {
 let row=["雪"+"😀","é"]
 let rows=[row,row]
 let selected=slice(rows,0,1)
 print(rows); print(first(rows)); print(selected); print(len(selected))
 print(rows==[["雪😀","é"],["雪😀","é"]])
 print(rows!=[["雪😀","x"],["雪😀","é"]])
 let empty: [int]=[]
 print(empty); print(slice(empty,0,0)); print(len(empty))
}
'''
        response = self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_packed_integer_leaf_promotions_preserve_snapshots(self):
        source='''effect fn main() {
 var values=arrayRepeat([0,255],4500)
 let bytes=values
 values[31]=-9223372036854775807-1
 values[32]=9223372036854775807
 values[8192]=-1
 let wide=values
 values[31]=255
 values[32]=0
 values[8192]=1
 values.extend([256,-256,255,0])
 print(bytes[31]); print(bytes[32]); print(bytes[8192])
 print(wide[31]); print(wide[32]); print(wide[8192])
 print(values[31]); print(values[32]); print(values[8192])
 print(slice(values,8998,9004))
 print(values.pop()); print(len(values)); print(bytes==wide)
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_literal_leaf_boundaries_preserve_values_and_nested_ownership(self):
        declarations = []
        checks = []
        for count in (0, 1, 255, 256, 257):
            values = ','.join(str(i % 256) for i in range(count))
            declarations.append(f'var a{count}: [int] = [{values}]; let old{count} = a{count}')
            checks.append(f'a{count}.append(-9223372036854775808); print(len(a{count})); print(old{count}); print(a{count}[{count}])')
        source = 'effect fn main(){' + ';'.join(declarations + checks) +             '; let text="雪"+"😀"; var nested=[[text],[text]]; let before=nested; nested[0].append("new"); print(before); print(nested) }'
        response = self.compiler.request(dict(version=1, operation='compile', files={'main.pxl': source}))
        self.assertEqual(response['status'], 'ok', response)
        code = response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable = build_native(code, Path(directory)/'program')
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()], VM(code).run())

    def test_large_byte_decode_uses_compact_storage_and_copy_on_write(self):
        source='''effect fn run()->Result[int,Error] {
 var bytes=zlibDecompress(readBytes("packed",100000)?,20000000)?
 let before=bytes
 bytes[0]=256
 bytes[19999999]=-1
 print(len(bytes)); print(before[0]); print(before[19999999])
 print(bytes[0]); print(bytes[19999999]); print(bytes[10000000])
 return .Ok(0)
}
effect fn main(){ print(run()) }
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'packed').write_bytes(zlib.compress(bytes([255])*20000000))
            executable=build_native(code,root/'program')
            result=subprocess.run([str(executable),'--read-root',str(root)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            values=[json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(values[:6],[20000000,255,255,256,-1,255])
            self.assertEqual(values[6],{'variant':'Ok','fields':{'value':0}})

    def test_last_use_releases_large_locals_before_next_allocation(self):
        source='''effect fn main() {
 let first=arrayRepeat([7],12000000)
 print(len(first))
 let second=arrayRepeat([9],12000000)
 print(second[11999999])
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(response['result']['bytecode'],Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],[12000000,9])
            with patch('pixellang.native_codegen._dead_local_releases',return_value={}):
                baseline=build_native(response['result']['bytecode'],Path(directory)/'baseline')
            failed=subprocess.run([str(baseline),'--memory-profile'],capture_output=True,text=True,timeout=30)
            self.assertEqual(failed.returncode,70,failed.stderr)
            self.assertIn('Array storage budget exceeded',failed.stderr)
            failure=json.loads(failed.stderr)
            self.assertEqual(sum(item['bytes'] for item in failure['allocationBuckets'].values()),failure['arrayBytes'])

    def test_escaping_closures_and_function_containers(self):
        source = '''record Callback { run: fn() -> string }
fn make(value: string) -> fn() -> fn() -> string {
 return fn() -> fn() -> string = fn() -> string = value
}
fn identity[T](value: T) -> T = value
effect fn apply(value: int, callback: effect fn(int)->int) -> int = callback(value)
fn logger(value: string) -> effect fn() = effect fn() { print(value) }
effect fn main() {
 var value="雪"+"😀"
 let first=make(value)
 let callbacks=[Callback{run:first()},Callback{run:make("other")()}]
 let log=logger(value)
 value="replaced"
 print(callbacks[0].run()); print(callbacks[1].run())
 log(); print(apply(7,identity::[int]))
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_text_pipeline_retains_input_and_returns_owned_results(self):
        source = '''fn render(parts: [string]) -> string = replace(trim(join(parts,"|")),"|","雪")
effect fn main() {
 let parts=["  "+"one","two","😀  "]
 var output=render(parts)
 print(parts); print(output)
 var i=0
 while i<100 { output=replace(output,"雪","雪"); i+=1 }
 print(output); assert(output=="one雪two雪😀")
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_binary_encoding_pipeline_from_source(self):
        source = '''fn checksum(text: string) -> int = crc32(utf8Encode(text))
effect fn main() {
 let text="雪"+"😀"
 let bytes=utf8Encode(text)
 print(bytes); print(checksum(text)); print(crc32(bytes))
 print(float64Bytes(-0.0)); print(float64Bytes(1.5))
 let empty:[int]=[]
 print(crc32(empty)); print(checksum("123456789"))
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_checked_binary_decoding_and_error_propagation(self):
        source = '''fn decode(bytes:[int]) -> Result[[string],Error] = .Ok([utf8Decode(bytes)?])
effect fn main() {
 print(decode(utf8Encode("雪😀")))
 print(decode([192,128]))
 print(utf8Decode([256])); print(utf8Decode([237,160,128]))
 print(utf8Decode([244,144,128,128])); print(utf8Decode([226,130]))
 print(utf8Decode([128])); print(utf8Decode([226,65,172]))
 print(utf8Decode([240,128,128,128])); print(utf8Decode([224,128,128]))
 let empty:[int]=[]
 print(utf8Decode(empty)); print(float64FromBytes(empty))
 print(float64FromBytes([-1])); print(float64FromBytes(float64Bytes(-0.0)))
 print(float64FromBytes(float64Bytes(1.5)))
 print(float64FromBytes([127,240,0,0,0,0,0,0]))
 print(float64FromBytes([127,248,0,0,0,0,0,0]))
 print("continues after errors")
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_decimal_parsing_boundaries_errors_and_float_bits(self):
        integers=['0','-0','+42','9223372036854775807','-9223372036854775808',
                  '9223372036854775808','-9223372036854775809','000000000000000000001',
                  '0000000000000000000001','','+',' 1','1\\n','1_0','１２','1.0','0x10']
        floats=['0','-0','+1.25','1e3','1E-3','5e-324','2.2250738585072014e-308',
                '1.7976931348623157e308','1.7976931348623159e308','1e-999','-1e-999',
                '9007199254740993','0.1000000000000000055511151231257827021181583404541015625',
                '','+','.5','1.','1e','1e+','NaN','Infinity','0x1p0',' 1','１','1_0']
        source='fn bits(text:string)->Result[[int],Error] = .Ok(float64Bytes(parseFloat64(text)?))\n'
        source+='effect fn main() {\n'
        source+='\n'.join('print(parseInt('+json.dumps(value)+'))' for value in integers)
        source+='\n'+'\n'.join('print(bits('+json.dumps(value)+'))' for value in floats)
        source+='\nprint("continues")\n}\n'
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_native_input_and_capability_scoped_file_roundtrip(self):
        source = '''fn status[T](value:Result[T,Error])->string = match value {
 .Ok(result)=>"ok", .Err(error)=>error.code
}
effect fn main() {
 print(input()); print(input())
 print(writeText("nested/雪.txt","雪😀")); print(readText("nested/雪.txt"))
 print(writeText("nested/雪.txt","replaced")); print(readText("nested/雪.txt"))
 print(writeBytes("binary",[0,128,255])); print(readBytes("binary",3))
 print(status(readBytes("binary",2))); print(status(readText("binary")))
 print(status(readText("missing"))); print(status(readText("../outside")))
 print(status(readText("/absolute"))); print(status(readText("nested")))
 print(status(writeText("nested","bad"))); print(status(writeBytes("bad",[256])))
 print(status(readBytes("binary",-1)))
 print(writeBytes("empty",[])); print(readBytes("empty",0))
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'nested').mkdir()
            executable=build_native(code,root/'program')
            result=subprocess.run([str(executable),'--read-root',str(root),'--write-root',str(root)],
                                  input='request 雪😀\n',capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            expected=VM(code,input_text='request 雪😀\n',file_access=FileAccess(root,root)).run()
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],expected)
            self.assertEqual((root/'nested/雪.txt').read_text(),'replaced')
            self.assertEqual((root/'binary').read_bytes(),bytes([0,128,255]))
            self.assertFalse(list(root.glob('.pixel-native-*')))

    def test_native_file_denials_symlinks_and_budget_preserve_target(self):
        source = '''fn status[T](value:Result[T,Error])->string = match value {
 .Ok(result)=>"ok", .Err(error)=>error.code
}
effect fn main() {
 print(status(readText("link"))); print(status(writeText("link","changed")))
 print(status(readText("linked/file"))); print(status(writeText("linked/file","changed")))
 print(status(readText("../outside/file"))); print(status(writeText("../outside/file","changed")))
 print(status(writeText("target","changed")))
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); root=base/'root'; root.mkdir(); outside=base/'outside'; outside.mkdir()
            (outside/'file').write_text('original'); (root/'target').write_text('original')
            (root/'link').symlink_to(outside/'file'); (root/'linked').symlink_to(outside,target_is_directory=True)
            executable=build_native(code,base/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],['io.access_denied']*7)
            result=subprocess.run([str(executable),'--read-root',str(root),'--write-root',str(root)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            values=[json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(values[0],'io.access_denied'); self.assertEqual(values[1],'io.invalid_file')
            self.assertTrue(all(value in ('io.invalid_path','io.access_denied') for value in values[2:4]))
            self.assertEqual(values[4:],['io.invalid_path','io.invalid_path','ok'])
            self.assertEqual((outside/'file').read_text(),'original')
            (root/'target').write_text('original')
            result=subprocess.run([str(executable),'--write-root',str(root),'--io-budget','8192'],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,70,result.stderr)
            self.assertIn('File operation budget exceeded',result.stderr)
            self.assertEqual((root/'target').read_text(),'original')
            self.assertFalse(list(root.glob('.pixel-native-*')))

    def test_native_atomic_write_target_named_like_temporary_and_preflight_budget(self):
        source='effect fn main() { print(writeText(input(),"safe")) }'
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); executable=build_native(code,root/'program')
            with subprocess.Popen([str(executable),'--write-root',str(root)],stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as process:
                name=f'.pixel-native-{process.pid}-1'
                stdout,stderr=process.communicate(input=name,timeout=10)
                self.assertEqual(process.returncode,0,stderr)
                self.assertEqual((root/name).read_text(),'safe')
            (root/'target').write_text('original')
            result=subprocess.run([str(executable),'--write-root',str(root),'--io-budget','8192'],
                                  input='target',capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,70,result.stderr)
            self.assertIn('File operation budget exceeded',result.stderr)
            self.assertEqual((root/'target').read_text(),'original')
            self.assertEqual([path.name for path in root.glob('.pixel-native-*')],[name])

    def test_native_chunked_reads_charge_actual_bytes_and_decode_unicode(self):
        source='''fn byteCount(value:Result[[int],Error])->int = match value { .Ok(items)=>len(items), .Err(error)=>-1 }
fn textCount(value:Result[string,Error])->int = match value { .Ok(text)=>len(text), .Err(error)=>-1 }
effect fn main() { print(byteCount(readBytes("data",90000))); print(textCount(readText("data"))) }
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'data').write_bytes(('雪'*30000).encode())
            executable=build_native(response['result']['bytecode'],root/'program')
            for budget,success in [(196384,True),(196383,False)]:
                result=subprocess.run([str(executable),'--read-root',str(root),'--io-budget',str(budget)],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0 if success else 70,result.stderr)
                if success:self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],[90000,30000])
                else:self.assertIn('File operation budget exceeded',result.stderr)

    def test_native_json_conversion_preserves_shapes_snapshots_and_escaping(self):
        source = '''record Row { name:string, values:[int] }
enum Choice { Row(value:Row), Empty }
fn encode[T](value:T)->json = jsonFrom(value)
effect fn main() {
 var row=Row{name:"雪😀",values:[1,2]}
 let old=encode(row)
 row.values[0]=9
 print(old); print(jsonStringify(old)); print(jsonKind(old)); print(len(old))
 print(encode(row)); print(encode(Choice.Row(row))); print(encode(Choice.Empty))
 let present:Option[int]=.Some(7)
 let absent:Option[int]=.None
 print(encode(present)); print(encode(absent))
 var table=map[string,Row]{"first":row,"second":row}
 let ignored=table.remove("first")
 print(encode(table))
 print(encode(map[int,string]{3:"three",1:"one"}))
 let documents=[jsonNull(),encode(9223372036854775807),encode(-9223372036854775808),encode(true),encode("text")]
 print(encode(documents)); print(documents); print(len(encode(documents)))
 print(jsonKind(jsonNull())); print(jsonKind(encode(false))); print(jsonKind(encode(1)))
 print(jsonKind(encode("雪😀"))); print(len(encode("雪😀")))
 print(encode(encode(row)))
}
'''
        escaped='"\\\n\t\b\f\r'+chr(0)+chr(31)+'雪😀'
        source=source.replace(' print(encode(encode(row)))',' print(encode('+json.dumps(escaped)+'))\n print(encode(encode(row)))')
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_native_json_parse_accessors_exact_numbers_and_error_cleanup(self):
        valid=['null','true','false','[]','{}',' [1,2,{"雪":"😀"}] ',
               '"\\ud83d\\ude00"','"\\b\\f\\n\\r\\t\\u0000\\\\\\/\\\""',
               '{"b":2,"a":1}','1234567890123456789012345678901234567890','1.00e+003']
        invalid=['',' ','truefalse','[1,]','{"a":1,}','01','-01','.1','1.','1e+','NaN','Infinity',
                 '"\\x20"','"\\ud800"','"\\udc00"','{"a":1,"\\u0061":2}',
                 '{"ok":[{"text":"owned"}],"bad":[1,2,]}']
        numbers=['9007199254740993','9223372036854775807','-9223372036854775808',
                 '9223372036854775808','-9223372036854775809','92233720368547758070e-1',
                 '1.20e1','1.2','1.00','0e999999','1e999999','1e-999999',
                 '-0.0','1e309','5e-324','1e-999']
        source='''fn status[T](value:Result[T,Error])->string = match value {
 .Ok(result)=>"ok", .Err(error)=>error.code+"|"+error.expected+"|"+error.actual+"|"+error.path+"|"+error.operation
}
fn document(text:string)->json = match jsonParse(text) { .Ok(value)=>value, .Err(error)=>jsonNull() }
fn bits(value:json)->Result[[int],Error] = .Ok(float64Bytes(jsonAsFloat64(value)?))
effect fn main() {
'''
        source+='\n'.join('print(jsonParse('+json.dumps(value)+'))' for value in valid)
        source+='\n'+'\n'.join('print(status(jsonParse('+json.dumps(value)+')))' for value in invalid)
        for number in numbers:
            literal=json.dumps(number)
            source+='\nprint(status(jsonAsInt(document('+literal+')))); print(jsonAsInt(document('+literal+')));'
            source+='\nprint(status(bits(document('+literal+')))); print(bits(document('+literal+')));'
        source+=r'''
 let obj=document("{\"a\":[true,\"text\"],\"b\":0}")
 print(jsonKeys(obj)); print(jsonGet(obj,"a")); print(status(jsonGet(obj,"雪")))
 let array=document("[true,\"text\"]")
 print(jsonAt(array,0)); print(jsonAt(array,1)); print(status(jsonAt(array,-1))); print(status(jsonAt(array,2)))
 print(status(jsonGet(array,"a"))); print(status(jsonKeys(array))); print(status(jsonAt(obj,0)))
 print(status(jsonAsBool(obj))); print(status(jsonAsString(array))); print(status(jsonAsInt(jsonNull())))
 print(jsonAsBool(document("true"))); print(jsonAsString(document("\"owned\"")))
 var i=0
 while i<1000 { let ignored=jsonParse("{\"ok\":[{\"text\":\"owned\"}],\"bad\":[1,2,]}"); i+=1 }
 print("alive after failures")
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_native_scalar_text_split_repeat_and_unicode_categories(self):
        import random
        rng=random.Random(448)
        points=[0,9,32,65,97,0x301,0x378,0x200d,0x2028,0x2165,0xe000,0x1f600,0x31350,0x10ffff]
        points += [rng.randrange(0xe000,0x110000) for _ in range(80)]
        source='''record Row { values:[int] }
effect fn main() {
 print(string(9223372036854775807)); print(string(-9223372036854775808))
 print(string(true)); print(string(false)); print(string("雪😀"))
 print(string(-0.0)); print(string(0.1)); print(string(1e16)); print(string(1e-5))
 print(split("",",")); print(split("a,,b,",",")); print(split(",",","))
 print(split("aaaaa","aa")); print(split("ababab","abab")); print(split("雪😀雪😀","😀"))
 print(split("short","longer separator"))
 let base=[Row{values:[1,2]}]
 var repeated=arrayRepeat(base,3)
 let before=repeated
 repeated[0].values[0]=9
 print(base); print(before); print(repeated); print(arrayRepeat(base,0))
 let empty:[int]=[]
 print(arrayRepeat(empty,9223372036854775807)); print(arrayRepeat([1,2],1))
 print(len(arrayRepeat([0],32769)))
 var text="a"
 var i=0
 while i<15 { text=text+text; i+=1 }
 print(len(split(text+"b",text+"c")))
 var j=0
 while j<500 { let category=unicodeCategory("雪"); j+=1 }
'''
        source+='\n'.join('print(unicodeCategory('+json.dumps(chr(point))+'))' for point in points)
        source+='\n}\n'
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_decoded_integer_arrays_preserve_snapshots_across_leaf_boundaries(self):
        source = r'''effect fn main() {
 for count in [0, 1, 255, 256, 257] {
  let text = jsonStringify(jsonFrom(arrayRepeat([255], count)))
  match jsonDecode::[[int]](text) {
   .Ok(original) => {
    var changed = original
    changed.append(-9223372036854775807-1)
    changed.append(9223372036854775807)
    let snapshot = changed
    changed[count] = 0
    print(len(original)); print(snapshot[count]); print(changed[count])
    print(changed.pop()); print(jsonStringify(jsonFrom(slice(snapshot, count, count+2))))
   },
   .Err(error) => { print(error.code) }
  }
 }
 match jsonDecode::[[[int]]]("[[1,2],[3,4]]") {
  .Ok(original) => {var changed=original;changed[0][0]=300;print(original);print(changed)},
  .Err(error) => {print(error.code)}
 }
}
'''
        response = self.compiler.request(dict(version=1, operation='compile', files={'main.pxl': source}))
        self.assertEqual(response['status'], 'ok', response)
        code = response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable = build_native(code, Path(directory)/'program')
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()], VM(code).run())

    def test_native_typed_json_decode_records_enums_maps_and_error_paths(self):
        source='''record Row { name:string, values:[int], note:Option[string] }
enum Choice { Empty, Row(value:Row) }
record Node { next:Option[Node] }
fn status[T](value:Result[T,Error])->string = match value {
 .Ok(result)=>"ok", .Err(error)=>error.code+"|"+error.expected+"|"+error.actual+"|"+error.path+"|"+error.operation
}
fn document(text:string)->json = match jsonParse(text) { .Ok(value)=>value, .Err(error)=>jsonNull() }
effect fn main() {
'''
        good=[('Row',{'values':[1,2],'name':'雪'}),('Row',{'name':'x','values':[],'note':'owned'}),
              ('Choice',{'variant':'Empty','fields':{}}),('Choice',{'variant':'Row','fields':{'value':{'name':'x','values':[3]}}}),
              ('map[string,Row]',{'a':{'name':'first','values':[1]}}),
              ('map[int,string]',[{'key':2,'value':'two'},{'key':1,'value':'one'}]),
              ('map[string,json]',{'a':[1,2],'b':True}),('Node',{'next':{'next':None}}),
              ('[[int]]',[[1,2],[]]),('Option[int]',None),('Option[int]',7),('json',{'keep':[1,2]})]
        for typ,value in good:
            text=json.dumps(json.dumps(value,ensure_ascii=False),ensure_ascii=False)
            source+=f'print(jsonDecode::[{typ}]({text})); print(jsonDecode::[{typ}](document({text})));\n'
        bad=[('Row',{}),('Row',{'name':'x','values':[],'extra':0}),('Row',{'name':'x','values':[1,'bad']}),
             ('Row',{'name':'x','values':[],'雪':1}),('Row',{'name':'x','values':[],'a.b':1}),
             ('Choice',{'variant':'Unknown','fields':{}}),('Choice',{'variant':'Row','fields':{}}),
             ('Choice',{'variant':'Row','fields':{'value':{'name':'x','values':[None]}}}),
             ('map[string,[int]]',{'a.b':[1,False]}),('map[string,[int]]',{'雪':[1,False]}),
             ('map[int,string]',[{'key':1,'value':'first'},{'key':1.0,'value':'second'}]),
             ('map[float64,int]',[{'key':-0.0,'value':1},{'key':0.0,'value':2}]),
             ('map[int,string]',[{'key':1}]),('int',9223372036854775808),('[int]',[1,False]),('bool',0)]
        for typ,value in bad:
            text=json.dumps(json.dumps(value,ensure_ascii=False),ensure_ascii=False)
            source+=f'print(status(jsonDecode::[{typ}]({text})));\n'
        source+=r'''
 print(status(jsonDecode::[Row]("not json")))
 var i=0
 while i<200 {
  let ignored=jsonDecode::[[Row]]("[{\"name\":\"owned\",\"values\":[1,2]},{\"name\":\"second\",\"values\":[3,false]}]")
  i+=1
 }
 print("alive after decode failures")
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_checked_compression_streams_limits_and_roundtrip(self):
        packed=zlib.compress(b'hello')
        cases=[(b'',100),(b'bad',100),(packed,4),(packed,5),
               (packed[:-1],100),(packed+b'\x00',100),(packed+packed,100),
               (packed[:-1]+bytes([packed[-1]^1]),100),(zlib.compress(b''),0)]
        checks='\n'.join('print(zlibDecompress(['+','.join(map(str,data))+f'],{limit}))' for data,limit in cases)
        source = '''fn roundtrip(bytes:[int]) -> Result[int,Error] {
 let packed=zlibCompress(bytes)?
 let restored=zlibDecompress(packed,len(bytes))?
 return .Ok(crc32(restored))
}
effect fn main() {
 var text="x"
 var i=0
 while i<17 { text=text+text; i+=1 }
 print(roundtrip(utf8Encode(text)))
 print(zlibCompress(utf8Encode("hello")))
 print(zlibCompress([256])); print(zlibDecompress([-1],10))
 let empty:[int]=[]
 print(zlibDecompress(empty,-1))
CHECKS
 print("continues after errors")
}
'''.replace('CHECKS',checks)
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_persistent_append_with_owned_values_and_escaping_snapshots(self):
        source = '''record Row { labels:[string] }
effect fn main() {
 var rows:[Row]=[]
 var snapshots:[[Row]]=[]
 var i=0
 while i<1100 {
  rows.append(Row{labels:["雪"+"😀"]})
  if i==31 || i==1023 { snapshots.append(rows) }
  i+=1
 }
 let read=fn()->[Row]=rows
 rows.append(Row{labels:["last"]})
 print(len(read())); print(len(rows))
 print(len(snapshots[0])); print(len(snapshots[1]))
 print(snapshots[0][31]); print(rows[1100])
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_batch_extend_self_alias_and_pop_tree_shrinking(self):
        source = '''effect fn main() {
 var values=["雪"+"😀"]
 var i=0
 while i<11 { values.extend(values); i+=1 }
 let before=values
 let empty:[string]=[]
 values.extend(empty)
 values.extend(["last"])
 print(len(before)); print(len(values)); print(values.pop())
 i=0
 while i<2048 {
  let removed=values.pop()
  if i==0 || i==1023 || i==2015 || i==2047 { print(removed) }
  i+=1
 }
 print(values.pop()); print(len(values)); print(len(before)); print(before[2047])
 values.extend(["rebuilt","tail"])
 print(values); print(values.pop()); print(values)
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_nested_array_record_updates_preserve_aliases_and_captures(self):
        source = '''record Row { name:string, cells:[[string]] }
effect fn index(value:int)->int { print(value); return value }
effect fn main() {
 var rows=[Row{name:"original",cells:[["雪"+"😀"],["old"]]}]
 let before=rows
 let read=fn()->[Row]=rows
 rows[index(0)].cells[index(1)][index(0)]="new"+" value"
 print(rows); print(before); print(read())
 rows[0]=rows[0] with {name:"changed"}
 print(rows); print(before)
 var numbers:[int]=[]
 var i=0
 while i<1100 { numbers.append(i); i+=1 }
 let saved=numbers
 numbers[1024]=999
 print(saved[1024]); print(numbers[1024]); print(numbers[1023]); print(numbers[1025])
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_scoped_inout_reborrowing_and_nested_writeback(self):
        source = '''record Row { values:[string] }
fn append(values:inout [string]) { values.append("雪"+"😀") }
fn edit(row:inout Row) { append(&row.values); row.values[0]="changed" }
fn again(row:inout Row) { edit(&row); append(&row.values) }
effect fn main() {
 var rows=[Row{values:["old"]}]
 let before=rows
 again(&rows[0])
 print(rows); print(before)
 append(&rows[0].values)
 print(rows)
 var left=1
 var right=2
 swap(&left,&right)
 print(left); print(right)
}
fn swap(left:inout int,right:inout int) { let saved=left; left=right; right=saved }
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_deep_reborrows_preserve_outer_locks_and_release_sibling_calls(self):
        source = '''fn identity(value:int)->int = value
fn visit(values:inout [int], depth:int) {
 let amount=identity(1)
 if depth>0 { visit(&values,depth-1) }
 values[0]+=amount
}
effect fn main() {
 let original=[1]
 var values=original
 visit(&values,32)
 print(values);print(original)
 visit(&values,0)
 print(values)
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        expected=VM(code).run()
        self.assertEqual(expected,[[34],[1],[35]])
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],expected)

    def test_inout_closure_arguments_and_early_propagation_release(self):
        source = '''fn apply(value:inout int, action:fn(inout int)) { action(&value) }
fn stop(value:inout int)->Option[int] {
 value+=1
 let empty:Option[int]=.None
 let result=empty?
 return .Some(result)
}
effect fn main() {
 var value=1
 let amount=3
 apply(&value,fn(target:inout int) { target+=amount })
 print(value); print(stop(&value)); print(value)
 value=9
 apply(&value,fn(target:inout int) { target+=amount })
 print(value)
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_typed_maps_lookup_iteration_and_order_independent_equality(self):
        source = '''enum Code { Id(value:int), Name(value:string), Empty }
fn read[K:Hashable,V](table:map[K,V],key:K)->Option[V] = table.get(key)
effect fn main() {
 let table=map[int,string]{3:"three",1:"one"}
 print(table); print(table.keys()); print(table[1]); print(read(table,3)); print(read(table,9))
 print(table.has(1)); print(table.has(2))
 for key,value in table { print(key); print(value) }
 for key in table { print(key) }
 print(table==map[int,string]{1:"one",3:"three"})
 print(table!=map[int,string]{1:"one",3:"other"})
 let codes=map[Code,[int]]{Code.Id(7):[1,2],Code.Name("雪"+"😀"):[3],Code.Empty:[]}
 print(codes); print(codes[Code.Name("雪😀")]); print(codes.keys())
 print(map[float64,int]{-0.0:7}[0.0]); print(map[bool,int]{true:1,false:2}[false])
 let nested=map[string,map[int,string]]{"first":table}
 print(nested); print(nested==map[string,map[int,string]]{"first":map[int,string]{1:"one",3:"three"}})
 let empty=map[int,string]{}
 print(len(empty)); print(read(empty,1))
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_map_path_updates_and_nested_inout_preserve_snapshots(self):
        source = '''record Row { values:map[string,[int]] }
fn bump(values:inout [int]) { values.append(9); values[0]+=4 }
fn touch(table:inout map[int,Row]) { bump(&table[7].values["scores"]) }
effect fn main() {
 var table=map[int,Row]{}
 table[7]=Row{values:map[string,[int]]{"scores":[1,2]}}
 let old=table
 touch(&table)
 table[7].values["new"]=[3]
 table[1]=Row{values:map[string,[int]]{}}
 print(table); print(old); print(table.keys()); print(table[7].values.keys())
 table[7]=old[7]
 print(table.keys()); print(table[7])
 var values=map[int,int]{}
 var i=0
 while i<1100 { values[i]=i; i+=1 }
 let before=values
 values[1024]=999
 print(before[1024]); print(values[1024]); print(values[1023]); print(len(values))
 var zeros=map[float64,int]{-0.0:1}
 zeros[0.0]=2
 print(zeros); print(len(zeros))
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_map_removal_compaction_reinsertion_and_borrowed_snapshots(self):
        source = '''record Row { values:map[int,string] }
fn drop(values:inout map[int,string],key:int)->Option[string] = values.remove(key)
effect fn main() {
 var row=Row{values:map[int,string]{}}
 var i=0
 while i<100 { row.values[i]="雪"+"😀"; i+=1 }
 let original=row
 print(drop(&row.values,50)); print(drop(&row.values,500))
 i=0
 while i<75 { let ignored=drop(&row.values,i); i+=1 }
 print(len(row.values)); print(row.values.keys()); print(row.values)
 var expected=map[int,string]{}
 i=99
 while i>=75 { expected[i]="雪😀"; i-=1 }
 print(row.values==expected)
 row.values[0]="again"
 row.values[80]="updated"
 print(row.values.keys()); print(row.values[80]); print(original.values[50])
 let before=row.values
 for key in before { let ignored=drop(&row.values,key) }
 print(row.values); print(len(before)); print(before.keys())
 row.values[2]="reused"; print(row.values)
 var zeros=map[float64,int]{-0.0:1}
 print(zeros.remove(0.0)); print(zeros)
 var words=map[string,int]{"one":1,"two":2,"three":3}
 print(words.remove("two")); print(words)
 words["two"]=4; print(words); print(words.keys())
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_generic_records_field_order_and_nested_ownership(self):
        source = '''record Box[T] { name: string, items: [T] }
fn label(box: Box[int]) -> string = box.name
effect fn main() {
 let box=Box::[int]{items:[1,2],name:"雪"+"😀"}
 let rows=[box,box]
 print(box); print(rows[1].items); print(label(rows[0]))
 print(box==Box::[int]{name:"雪😀",items:[1,2]})
 print(box!=Box::[int]{name:"other",items:[1,2]})
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_deep_recursive_records_compare_and_release_iteratively(self):
        source = '''record Node { value: int, children: [Node] }
fn chain(count: int) -> Node {
 var current=Node{value:0,children:[]}
 var i=0
 while i<count { current=Node{value:i,children:[current]}; i+=1 }
 return current
}
effect fn main() {
 let first=chain(5000)
 let second=chain(5000)
 print(first==second); print(first.value)
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable),'1000000'],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],[True,4999])
            self.assertEqual(VM(code,max_steps=1000000).run(),[True,4999])

    def test_generic_enums_and_match_payloads(self):
        source = '''enum Choice[T] { Empty, Items(values: [T]) }
record Holder { choice: Choice[string] }
fn unpack(value: Choice[string]) -> [string] {
 return match value { .Items(values)=>values, .Empty=>["empty"] }
}
effect fn main() {
 let value=Holder{choice:Choice::[string].Items(["雪"+"😀"])}
 print(value); print(unpack(value.choice)); print(unpack(Choice::[string].Empty))
 print(value.choice==Choice::[string].Items(["雪😀"]))
 print(value.choice!=Choice::[string].Empty)
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_propagation_retargets_types_and_releases_pending_values(self):
        source = '''record Failure { items: [string] }
fn wrap[T,E](value: Result[T,E]) -> Result[[T],E] = .Ok([value?])
fn fallback[T](value: Option[T]) -> Option[[T]] = Option.Some([value?])
fn value(ok: bool) -> Result[string,string] {
 if ok { return .Ok("suc"+"cess") }
 return .Err("fail"+"ure")
}
fn consume(prefix: [string], item: string) -> string = item
fn evaluate(ok: bool) -> Result[string,string] = .Ok(consume(["pending"+"value"],value(ok)?))
effect fn main() {
 print(wrap(Result::[int,string].Ok(4)))
 print(wrap(Result::[string,int].Err(9)))
 print(wrap(Result::[int,Failure].Err(Failure{items:["err"+"or"]})))
 print(fallback(Option.Some(5))); print(fallback(Option::[string].None))
 print(evaluate(false)); print(evaluate(true))
}
'''
        response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],VM(code).run())

    def test_iteration_snapshots_survive_replacement_and_loop_transfers(self):
        source = '''fn total(values: [int]) -> int {
 var result=0
 for index, value in values {
  if index==1 { continue }
  result+=value
  if index==2 { break }
 }
 return result
}
effect fn main() {
 var values=[1,2,3,4]
 for value in values { values=[99]; print(value) }
 print(values); print(total([10,20,30,40]))
 var text="雪😀é"
 for index, character in text { text="changed"; print(index); print(character) }
 for character in "a😀" { print(character) }
 let empty: [int]=[]
 for value in empty { print(value) }
 print(text)
}
'''
        response = self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable=build_native(code,Path(directory)/'program')
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            expected=[1,2,3,4,[99],40,0,'雪',1,'😀',2,'é','a','😀','changed']
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()],expected)
            self.assertEqual(VM(code).run(),expected)

    def test_unicode_string_calls_aliases_and_repeated_replacement(self):
        source = '''fn join(a: string, b: string) -> string = a+b
effect fn main() {
 let original=join("雪","😀")
 var current=original
 var i=0
 while i<2000 { current=join(original,"é"); i+=1 }
 print(original); print(current); print(len(current))
 print(current[1]); print(slice(current,1,3)); print(original<current)
}
'''
        response = self.compiler.request(dict(version=1, operation='compile', files={'main.pxl':source}))
        self.assertEqual(response['status'], 'ok', response)
        code = response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable = build_native(code, Path(directory)/'program')
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()], VM(code).run())

    def test_float64_arguments_returns_and_rounding(self):
        source = '''fn scale(value: float64, factor: float64) -> float64 = value*factor
effect fn main() {
 print(scale(1.25,2.0)); print(-17.5%5.25)
 print(scale(0.0,-1.0)); print(1.0/10.0)
}
'''
        response = self.compiler.request(dict(version=1, operation='compile', files={'main.pxl':source}))
        self.assertEqual(response['status'], 'ok', response)
        code = response['result']['bytecode']
        with tempfile.TemporaryDirectory() as directory:
            executable = build_native(code, Path(directory)/'program')
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()], VM(code).run())
            self.assertEqual(result.stdout.splitlines()[2], '-0.0')

    def test_multifile_loops_recursion_and_short_circuit(self):
        files = {
            'math.pxl': '''export fn factorial(n: int) -> int {
 if n<=1 { return 1 }
 return n*factorial(n-1)
}
export fn sum(n: int) -> int {
 var i=0
 var total=0
 while i<n { total+=i; i+=1 }
 return total
}
''',
            'main.pxl': '''import "math.pxl" as math
effect fn touched() -> bool { print(999); return true }
effect fn main() {
 print(math.factorial(10)); print(math.sum(1000))
 print(false && touched()); print(true || touched())
 print(-17/5); print(-17%5)
}
''',
        }
        response = self.compiler.request(dict(version=1, operation='compile', files=files))
        self.assertEqual(response['status'], 'ok', response)
        code = response['result']['bytecode']
        expected = [3628800, 499500, False, True, -3, -2]
        self.assertEqual(VM(code).run(), expected)
        with tempfile.TemporaryDirectory() as directory:
            executable = build_native(code, Path(directory)/'program')
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10,
                                    cwd=directory, env={'PATH': '/usr/bin:/bin'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([json.loads(line) for line in result.stdout.splitlines()], expected)
