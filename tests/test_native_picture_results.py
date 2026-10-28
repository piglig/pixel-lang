import io,json,re,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from pixellang.vm import VM
from pixellang.picture import decode_picture
from tests.native_compiler import compile_native_sources
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS,SELFHOST_MAX_OUTPUT_CHARS
ROOT=Path(__file__).resolve().parents[1]
DRIVER='''import "picture.pxl" as picture
import "picture_reader.pxl" as reader
import "spatial_layout.pxl" as layout
record Request {encode:bool,source:layout.Source,bytes:[int]}
record Output {bytes:[int],source:layout.Source}
fn Process(request:Request)->Result[Output,Error]{
 var bytes=request.bytes
 if request.encode {bytes=picture.Encode(request.source,map[string,layout.Source]{},"main.pxl",4)?}
 let decoded=reader.Decode(bytes,1000000)?
 return .Ok(Output{bytes,source:decoded.root})
}
effect fn main(){
 match jsonDecode::[Request](input()) {
  .Ok(request)=>{match Process(request){.Ok(output)=>{print(output)},.Err(error)=>{print(error.message)}}},
  .Err(error)=>{print(error.message)}
 }
}'''
class NativePictureResultTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  files={'main.pxl':DRIVER};pending=re.findall(r'(?m)^import "([^"]+)"',DRIVER)
  while pending:
   name=pending.pop()
   if name in files:continue
   files[name]=(ROOT/'selfhost'/name).read_text()
   pending.extend(re.findall(r'(?m)^import "([^"]+)"',files[name]))
  cls.code=compile_native_sources(files)
 @staticmethod
 def source():
  return dict(magic='PIXELLANG',versions=dict(language='0.9',encoding='0.9',format='0.9'),encoding='rgba8',dimensions=[2,1],pixels=[dict(position=[0,0],rgba=[16,0,1,255]),dict(position=[1,0],rgba=[17,0,1,255])],spatial=dict(links=[{'kind':'next','from':[0,0],'to':[1,0]}]),metadata={})
 def run_request(self,source=None,data=None):
  request=dict(encode=data is None,source=source or self.source(),bytes=data or [])
  with patch('PIL.Image.open',side_effect=AssertionError('host image decoder in native path')):
   return VM(self.code,input_text=json.dumps(request),max_steps=40_000_000,max_output_chars=SELFHOST_MAX_OUTPUT_CHARS,**SELFHOST_HEAP_LIMITS).run()[0]
 def test_encode_decode_and_independent_picture_decoder(self):
  result=self.run_request()
  self.assertEqual(result['source'],self.source())
  data=bytes(result['bytes'])
  with Image.open(io.BytesIO(data)) as image:
   self.assertEqual(image.mode,'RGBA')
   # Independent implementation checks the native encoder's container contract.
   decoded=decode_picture(image)
  self.assertEqual(decoded['pixels'],self.source()['pixels'])
  self.assertEqual(decoded['spatial'],self.source()['spatial'])
 def test_corruption_returns_errors(self):
  self.assertIn('signature',self.run_request(data=[]))
  result=self.run_request();bad=result['bytes'];bad[29]^=1
  self.assertIn('CRC',self.run_request(data=bad))
 def test_invalid_document_returns_error(self):
  source=self.source();source['pixels'][0]['rgba'][0]=-1
  self.assertIn('bytes',self.run_request(source=source))
if __name__=='__main__':unittest.main()
