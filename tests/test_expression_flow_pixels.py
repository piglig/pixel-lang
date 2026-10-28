"""Expression flows survive native PNG delivery without source/name metadata."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from pixellang.compiler_service import CompilerService
from pixellang.codec import rgba
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources
from tests import test_native_pixel_delivery as delivery


class ExpressionFlowPixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        sources = {p.name:p.read_text() for p in (root/'selfhost').glob('*.pxl')}
        cls.code = compile_native_sources(sources)
        # Preserve the exact native candidate for related acceptance runs.
        encoded = json.dumps(cls.code,sort_keys=True,separators=(',',':')).encode()
        Path('/tmp/pixel-expression-flow-pixel-compiler.json').write_bytes(encoded)
        Path('/tmp/pixel-expression-flow-pixel-provenance.json').write_text(json.dumps(dict(
            sha256=hashlib.sha256(encoded).hexdigest(),
            sourceSha256=hashlib.sha256(json.dumps(sources,sort_keys=True,ensure_ascii=False).encode()).hexdigest())))

    def setUp(self): self.service = CompilerService(self.code)
    request = delivery.NativePixelDeliveryTests.request

    def roundtrip(self, files, expected):
        original = self.request('compile',files=files)['bytecode']
        self.assertEqual(VM(original).run(),expected)
        document = deepcopy(self.request('pixels',files=files)['document'])
        document['metadata'] = {}
        for module in document.get('bundle',{}).get('modules',{}).values(): module['metadata'] = {}
        image = self.request('export-pixel-png',document=document)['image']
        self.assertTrue(image.startswith(b'\x89PNG'))
        restored = self.request('compile-png',image=image)['bytecode']
        self.assertEqual(VM(restored).run(),expected)
        recovered = self.request('recover-png',image=image)
        rebuilt = self.request('compile',**recovered)['bytecode']
        self.assertEqual(VM(rebuilt).run(),expected)
        def shapes(code):
            return sorted((len(g['values']),len(g['children']),len(g['states']),g['stateful']) for g in code['flows'].values())
        self.assertEqual(shapes(original),shapes(restored))
        self.assertEqual(shapes(original),shapes(rebuilt))
        return recovered

    def test_forward_values_closures_and_record_updates(self):
        source = '''record Count {value:int}
fn identity[T](x:T)->T=x
flow Counter(seed:int) step(delta:int)->Count {
 state count=Count{value:seed}
 let updated=count with {value:apply(delta)}
 let apply=fn(x:int)->int=identity(count.value+x)
 next count=updated
 output updated
}
effect fn main(){let c=Counter(2);print(c.step(3).value);print(c.step(4).value)}'''
        self.roundtrip({'main.pxl':source},[5,9])

    def test_imported_children_conditional_steps_and_isolated_instances(self):
        files = {'child.pxl':'''export flow Counter(seed:int) step(delta:int)->int {
 state n=seed
 next n=n+delta
 output n
}''','main.pxl':'''import "child.pxl" as child
enum Event {Add(value:int),Skip}
flow Parent(seed:int) step(event:Event)->int {
 instance counter=child.Counter(seed)
 let result=match event {.Add(delta)=>counter.step(delta),.Skip=>-1}
 output result
}
effect fn main(){
 let a=Parent(2);let b=Parent(10)
 print(a.step(.Skip));print(a.step(.Add(3)));print(b.step(.Add(1)))
 print(a.step(.Skip));print(a.step(.Add(4)))
}'''}
        recovered = self.roundtrip(files,[-1,2,10,-1,5])
        self.assertIn('instance ','\n'.join(recovered['files'].values()))

    def test_stateless_child_parent_keeps_identity(self):
        self.roundtrip({'main.pxl':'''flow Child() step(x:int)->int {output x+1}
flow Parent() step(x:int)->int {instance child=Child();output child.step(x)}
effect fn main(){let p=Parent();print(p.step(4));print(p.step(8))}'''},[5,9])

    def test_named_debug_recovery_preserves_flow_bindings(self):
        files = {'main.pxl':'''flow Counter(seed:int) step(delta:int)->int {
 state count=seed
 let updated=count+delta
 next count=updated
 output updated
}
flow Parent() step(delta:int)->int {instance counter=Counter(2);output counter.step(delta)}
effect fn main(){let p=Parent();print(p.step(3))}'''}
        image = self.request('export-debug-png',files=files)['image']
        recovered = self.request('recover-png',image=image)
        text = '\n'.join(recovered['files'].values())
        self.assertIn('state count',text)
        self.assertIn('let updated',text)
        self.assertIn('instance counter',text)
        self.assertIn('counter.step(',text)
        self.assertEqual(VM(self.request('compile',**recovered)['bytecode']).run(),[5])

    def test_flow_only_pixel_members_reject_function_parent(self):
        document = self.request('pixels',files={'main.pxl':'''fn F(x:int)->int {
 return x
}
effect fn main(){print(F(3))}'''})['document']
        # Preserve a valid function header so rejection actually exercises the
        # flow-member parent rule, rather than malformed constructor syntax.
        member = next(p for p in document['pixels'] if p['rgba'] == rgba('keyword','return'))
        member['rgba'] = rgba('keyword','output')
        response = self.service.request(dict(version=1,operation='compile-pixels',document=document))
        self.assertNotEqual(response['status'],'ok',response)
        self.assertIn('Flow member requires a flow parent',json.dumps(response))


if __name__ == '__main__': unittest.main()
