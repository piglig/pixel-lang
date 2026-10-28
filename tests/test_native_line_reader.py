"""Explicit line-reader values support streaming without writable captures."""
from pathlib import Path
import tempfile
import unittest

from pixellang.compiler_client import CompilerClient
from pixellang.fileaccess import FileAccess
from pixellang.vm import VM


class NativeLineReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler=CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self,source):
        return self.compiler.request('compile',files={'main.pxl':source})['result']['bytecode']

    def test_construction_is_pure_and_does_not_access_files(self):
        code=self.compile('import "std/io.pxl" as io\nfn main(){let reader=io.Lines("missing.txt")}')
        self.assertEqual(VM(code).run(),[])

    def test_copied_cursor_positions_are_independent_and_eof_is_stable(self):
        code=self.compile('''import "std/io.pxl" as io
effect fn show(reader:inout io.LineReader) {
 match io.NextLine(&reader) { .Ok(value)=>{match value {Option.Some(line)=>{print(line.number);print(line.text)},Option.None=>{print("end")}}}, .Err(error)=>{print(error.message)} }
}
effect fn main(){
 var first=io.Lines("data.txt")
 show(&first)
 var second=first
 show(&first);show(&first);show(&second);show(&second);show(&first)
}''')
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'data.txt').write_text('one\ntwo')
            access=FileAccess(directory)
            self.assertEqual(VM(code,file_access=access).run(),[1,'one',2,'two','end',2,'two','end','end'])
            self.assertEqual(len(access.effects),1)

    def test_empty_trailing_lines_unicode_and_chunk_boundaries(self):
        code=self.compile('''import "std/io.pxl" as io
effect fn main(){
 var reader=io.Lines("data.txt")
 while true {match io.NextLine(&reader) {
  .Ok(value)=>{match value {Option.Some(line)=>{print(line.number);print(len(line.text))},Option.None=>{break}}}, .Err(error)=>{print(error.message);break}
 }}
}''')
        cases=[('',[1,0]),('\r\n',[1,0,2,0]),('猫🌍\rx\n',[1,2,2,1,3,0]),
               ('a'*65535+'\r\n猫🌍\rb\n',[1,65535,2,2,3,1,4,0]),
               ('a'*262144,[1,262144])]
        with tempfile.TemporaryDirectory() as directory:
            for text,expected in cases:
                with self.subTest(length=len(text)):
                    Path(directory,'data.txt').write_bytes(text.encode())
                    self.assertEqual(VM(code,file_access=FileAccess(directory),max_steps=1_000_000).run(),expected)

    def test_line_limit_keeps_already_processed_values(self):
        code=self.compile('''import "std/io.pxl" as io
effect fn main(){
 var reader=io.Lines("data.txt")
 var accepted:[string]=[]
 while true {match io.NextLine(&reader) {
  .Ok(value)=>{match value {Option.Some(line)=>{accepted.append(line.text)},Option.None=>{break}}},
  .Err(error)=>{print(error.message);break}
 }}
 print(accepted)
}''')
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'data.txt').write_text('ok\n'+'x'*262145)
            output=VM(code,file_access=FileAccess(directory),max_steps=1_000_000).run()
            self.assertEqual(output,['Line exceeds 262144 characters',['ok']])
