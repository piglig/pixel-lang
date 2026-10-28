"""Explicit generic value syntax survives native execution, PNG and editor paths."""
from tests.native_compiler import native_service
import unittest
from unittest.mock import patch

from pixellang.native_editor import NativeSymbolIndex
from pixellang.vm import VM

FILES={'main.pxl':'''import "library.pxl" as library
record Index {value:int}
fn identity[T](value:T)->T=value
effect fn main(){
 let box:library.Box[int]=library.Box::[int]{value:library.choose::[int](7,9)}
 let identity=[3,5]
 let Index=1
 print(identity[Index])
 print(box.value)
 print(library.choose::[[int]]([1,2],[3]))
}''','library.pxl':'''export record Box[T]{value:T}
export fn choose[T](left:T,right:T)->T=left
'''}

class NativeSpecializationDeliveryTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
     cls.compiler=native_service()
     cls.addClassCleanup(cls.compiler.close)
 def request(self,operation,**args):
  with patch('pixellang.text.parse_text',side_effect=AssertionError('host frontend forbidden')):
   response=self.compiler.request(dict(version=1,operation=operation,incremental=False,**args))
  self.assertEqual(response['status'],'ok',response)
  return response['result']
 def index(self,files):
  return NativeSymbolIndex(files,'main.pxl',self.request('editor-index',files=files)['index'])
 def test_native_execution_png_and_recovery_keep_specialization_distinct_from_indexing(self):
  expected=[5,7,[1,2]]
  self.assertEqual(VM(self.request('compile',files=FILES)['bytecode']).run(),expected)
  image=self.request('export-png',files=FILES)['image']
  self.assertEqual(VM(self.request('compile-png',image=image)['bytecode']).run(),expected)
  recovered=self.request('recover-png',image=image)
  self.assertTrue(any('::[' in text for text in recovered['files'].values()))
  self.assertEqual(VM(self.request('compile',**recovered)['bytecode']).run(),expected)
 def test_old_value_specialization_is_not_accepted_as_a_compatibility_form(self):
  for expression in ('identity[int](7)','Box[int]{value:7}'):
   source='record Box[T]{value:T}\nfn identity[T](value:T)->T=value\neffect fn main(){print('+expression+')}'
   response=self.compiler.request(dict(version=1,operation='compile',files={'main.pxl':source},incremental=False))
   self.assertEqual(response['status'],'diagnostics',response)
   self.assertTrue(response['diagnostics'])
 def test_specialized_function_values_and_enum_constructors_roundtrip(self):
  files={**FILES,'main.pxl':'''import "library.pxl" as library
enum Choice[T]{Empty,Value(item:T)}
effect fn main(){
 let choose=library.choose::[Choice[int]]
 let selected=choose(Choice::[int].Value(7),Choice::[int].Empty)
 match selected {.Value(item)=>{print(item)},.Empty=>{print(0)}}
}'''}
  self.assertEqual(VM(self.request('compile',files=files)['bytecode']).run(),[7])
  image=self.request('export-png',files=files)['image']
  self.assertEqual(VM(self.request('compile-png',image=image)['bytecode']).run(),[7])
  recovered=self.request('recover-png',image=image)
  self.assertEqual(VM(self.request('compile',**recovered)['bytecode']).run(),[7])
 def test_editor_navigation_and_unfinished_generic_signature(self):
  files={**FILES,'main.pxl':'import "library.pxl" as library\neffect fn main(){library.choose::[int](7, '}
  index=self.index(files)
  signature=index.signature_help('main.pxl',len(files['main.pxl']))
  self.assertIsNotNone(signature)
  self.assertEqual(signature['activeParameter'],1)
  self.assertEqual(len(signature['parameters']),2)
  definition=index.definition('main.pxl',files['main.pxl'].index('choose'))
  self.assertEqual(definition['source'],'library.pxl')
 def test_editor_completion_uses_native_type_context_after_explicit_marker(self):
  text='record Payload {value:int}\nfn identity[T](value:T)->T=value\nfn main(){let value=identity::[Pay'
  index=self.index({'main.pxl':text})
  items=index.complete('main.pxl',len(text),analyze=self.index)
  self.assertIn('Payload',[item['label'] for item in items])

if __name__=='__main__':unittest.main()
