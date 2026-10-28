"""Pixel codecs report malformed input as diagnostics or Result errors."""
import json
from pathlib import Path
import unittest
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT=Path(__file__).resolve().parents[1]
DRIVER='''import "pixel_decode.pxl" as decoder
import "literal_pixels.pxl" as literals
record Request {encode:bool,text:string,tokens:[decoder.Token]}
effect fn main(){
 match jsonDecode::[Request](input()) {
  .Ok(request)=>{
   if request.encode {
    match literals.Text(request.text) {
     .Ok(colors)=>{print(len(colors));print(colors[0])},
     .Err(error)=>{print(error.message)}
    }
   } else {print(decoder.ReadLiteral(request.tokens,0,"test.pixel"))}
  },
  .Err(error)=>{print(error.code)}
 }
}'''
MODULES=('pixel_decode.pxl','literal_pixels.pxl','pixel_tokens.pxl','spatial_layout.pxl','failures.pxl')
class NativeLiteralResultTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  files={'main.pxl':DRIVER,**{name:(ROOT/'selfhost'/name).read_text() for name in MODULES}}
  cls.code=compile_native_sources(files)
 def test_utf8_encoding_limit_counts_bytes(self):
  for text,size in [('界😀',7),('a'*65535,65535),('界'*21845,65535)]:
   output=VM(self.code,input_text=json.dumps(dict(encode=True,text=text,tokens=[])),max_steps=10_000_000,**SELFHOST_HEAP_LIMITS).run()
   self.assertEqual(output,[1+(size+1)//2,[19,size//256,size%256,255]])
  output=VM(self.code,input_text=json.dumps(dict(encode=True,text='😀'*16384,tokens=[])),max_steps=10_000_000,**SELFHOST_HEAP_LIMITS).run()
  self.assertEqual(output,['String literal exceeds 65535 UTF-8 bytes'])
 def test_malformed_words_and_encodings_are_diagnostics(self):
  def token(kind,value,index):return dict(kind=kind,value=value,position=[index,2])
  cases=[
   ([('string_head',1),('word',0xff00)],'UTF'),
   ([('string_head',1),('word',0x4101)],'padding'),
   ([('string_head',2),('word',65536)],'word'),
   ([('string_head',-1)],'word'),
   ([('string_head','bad')],'Expected'),
   ([('float_head',0),('word',0x7ff0),('word',0),('word',0),('word',0)],'finite'),
  ]
  for words,message in cases:
   with self.subTest(words=words):
    tokens=[token(kind,value,i) for i,(kind,value) in enumerate(words)]
    value=VM(self.code,input_text=json.dumps(dict(encode=False,text='',tokens=tokens)),max_steps=1_000_000).run()[0]
    self.assertEqual(len(value['diagnostics']),1,value)
    self.assertIn(message.lower(),value['diagnostics'][0]['message'].lower())
    self.assertEqual(value['next'],0)
 def test_valid_empty_unicode_and_integer_literals(self):
  from pixellang.codec import encode_token
  for kind,value in [('str',''),('str','界😀'),('int',-2**63),('float64',1.25)]:
   colors=encode_token(kind,value);tokens=[]
   for i,color in enumerate(colors):
    category,payload=color[0],color[1]*256+color[2]
    kinds={18:'integer_head',19:'string_head',20:'float_head',112:'word'}
    tokens.append(dict(kind=kinds[category],value=payload,position=[i,0]))
   result=VM(self.code,input_text=json.dumps(dict(encode=False,text='',tokens=tokens))).run()[0]
   self.assertEqual(result['diagnostics'],[])
   self.assertEqual(json.loads(result['value']),value)
   self.assertEqual(result['next'],len(colors))
if __name__=='__main__':unittest.main()
