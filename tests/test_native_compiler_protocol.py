import tempfile,time,unittest
from pathlib import Path
from pixellang.compiler_service import CompilerService
from pixellang.model import PixelError
from tests.native_compiler import compile_native_sources

ROOT=Path(__file__).resolve().parents[1]
class NativeCompilerProtocolTests(unittest.TestCase):
    def test_native_error_record_reaches_service_without_language_throw(self):
        files={'service_errors.pxl':(ROOT/'selfhost/service_errors.pxl').read_text(),
               'main.pxl':'''import "service_errors.pxl" as errors
effect fn main(){
 print("partial")
 errors.Report("input.pxl",Error{kind:"json",code:"json.type_mismatch",message:"Expected array",path:"$.tokens",operation:"jsonDecode",expected:"array",actual:"string"})
}'''}
        code=compile_native_sources(files)
        instructions=[i for fn in code['functions'].values() for i in fn['instructions']]
        self.assertFalse(any(i['op']=='TRY' or (i['op']=='BUILTIN' and i['arg']=='fail') for i in instructions))
        service=CompilerService(code)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PixelError) as caught:
                service._execute({},Path(directory),lambda:False,time.monotonic()+20)
        self.assertEqual((caught.exception.code,caught.exception.path,caught.exception.operation),('json.type_mismatch','$.tokens','jsonDecode'))
        self.assertEqual(service.vm.output,[])
        self.assertIsNone(service.vm.file_access)

if __name__=='__main__':unittest.main()
