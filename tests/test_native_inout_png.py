"""Inout modes and argument markers survive semantic PNG delivery and recovery."""
from tests.native_compiler import native_service
import unittest
from pixellang.vm import VM


class NativeInoutPNGTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler=native_service()
        cls.addClassCleanup(cls.compiler.close)

    def test_generic_and_higher_order_accesses_survive_png_recovery(self):
        source='''record Box { values: [int] }
fn replace[T](target: inout T,value:T) { target=value }
fn apply(values: inout [int],action: fn(inout [int])) { action(&values) }
effect fn main() {
 let old=Box{values:[1]}
 var changed=old
 apply(&changed.values,fn(items: inout [int]) { items.append(2) })
 replace(&changed.values[0],7)
 print(old.values); print(changed.values)
}'''
        exported=self.compiler.request(dict(version=1,operation='export-png',files={'main.pxl':source}))
        self.assertEqual(exported['status'],'ok',exported)
        image=exported['result']['image']
        compiled=self.compiler.request(dict(version=1,operation='compile-png',image=image))
        self.assertEqual(compiled['status'],'ok',compiled)
        self.assertEqual(VM(compiled['result']['bytecode']).run(),[[1],[7,2]])
        recovered=self.compiler.request(dict(version=1,operation='recover-png',image=image))
        self.assertEqual(recovered['status'],'ok',recovered)
        restored='\n'.join(recovered['result']['files'].values())
        self.assertIn('inout ',restored)
        self.assertIn('&',restored)
        rebuilt=self.compiler.request(dict(version=1,operation='compile',**recovered['result']))
        self.assertEqual(rebuilt['status'],'ok',rebuilt)
        self.assertEqual(VM(rebuilt['result']['bytecode']).run(),[[1],[7,2]])
