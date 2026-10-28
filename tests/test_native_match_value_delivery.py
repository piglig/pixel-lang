"""Match value blocks must remain editable native programs after PNG delivery."""
from tests.native_compiler import native_service
import re
import unittest
from unittest.mock import patch

from pixellang.vm import VM


class NativeMatchValueDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)

    def request(self, operation, **args):
        with patch('pixellang.text.parse_text', side_effect=AssertionError('host frontend used')):
            response = self.service.request(dict(version=1, operation=operation, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def roundtrip(self, source, expected):
        files = {'main.pxl': source}
        original = self.request('compile', files=files)['bytecode']
        self.assertEqual(VM(original).run(), expected)
        image = self.request('export-png', files=files)['image']
        restored_code = self.request('compile-png', image=image)['bytecode']
        self.assertEqual(VM(restored_code).run(), expected)
        self.assertEqual(len(original['functions']), len(restored_code['functions']))
        restored = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **restored)['bytecode']).run(), expected)
        self.assertFalse(any('value_body' in text or 'block_result' in text for text in restored['files'].values()))
        return restored

    def test_nested_value_blocks_preserve_local_values_and_editability(self):
        restored = self.roundtrip('''fn value(input:Option[int])->int = match input {
 Option.Some(n)=>{let added=n+10;match input {Option.Some(x)=>{added+x},Option.None=>0}},Option.None=>0
}
effect fn main(){print(value(.Some(2)))}''', [14])
        self.assertEqual(sum(len(re.findall(r'\b10\b', text)) for text in restored['files'].values()), 1)
        restored['files'] = {name: re.sub(r'\b10\b', '20', text) for name, text in restored['files'].items()}
        self.assertEqual(VM(self.request('compile', **restored)['bytecode']).run(), [24])

    def test_empty_and_explicit_discard_blocks_keep_unit_behavior(self):
        self.roundtrip('''effect fn report(input:Option[int]) = match input {
 Option.Some(n)=>{print(n);},Option.None=>{}
}
effect fn main(){report(.Some(7));report(.None)}''', [7])

    def test_propagation_keeps_function_boundary_after_recovery(self):
        self.roundtrip('''fn choose(value:Option[int])->Option[int] = match 1 {_=>{let n=value?;.Some(n+1)}}''' +
            '\neffect fn main(){print(match choose(.Some(2)){.Some(x)=>x,.None=>0});print(match choose(.None){.Some(x)=>x,.None=>4})}', [3,4])

    def test_propagation_releases_pending_write_after_recovery(self):
        self.roundtrip('''fn add(value:inout int,amount:int)->int{value+=amount;return value}
fn choose(input:Option[int])->Option[int]{
 var total=10
 let result=add(&total,match 1 {_=>{input?}})
 return .Some(result)
}
effect fn main(){print(match choose(.Some(4)){.Some(x)=>x,.None=>0});print(match choose(.None){.Some(x)=>x,.None=>7})}''', [14,7])

    def test_pixel_value_blocks_cannot_encode_a_nonlocal_return(self):
        from pixellang.codec import encode_token
        document=self.request('pixels', files={'main.pxl':'fn choose()->int=match 1{_=>{7}}\neffect fn main(){print(choose())}'})['document']
        tail,=encode_token('keyword','block_result')
        returned,=encode_token('keyword','return')
        replaced=0
        for pixel in document['pixels']:
            if pixel['rgba']==tail:
                pixel['rgba']=returned
                replaced+=1
        self.assertEqual(replaced,1)
        document['metadata']={}
        response=self.service.request(dict(version=1,operation='compile-pixels',document=document))
        self.assertEqual(response['status'],'diagnostics',response)
        self.assertTrue(any('Return cannot leave a value block' in d['message'] for d in response['diagnostics']),response)

    def test_flow_state_and_block_local_name_keep_separate_identities(self):
        self.roundtrip('''flow Counter() step(delta:int)->int {
 state current=0
 next current=match Option::[int].Some(delta) {
  Option.Some(n)=>{let current=n;current+1},Option.None=>current
 }
 output current
}
effect fn main(){let counter=Counter();print(counter.step(2));print(counter.step(4))}''', [0, 3])


if __name__ == '__main__':
    unittest.main()
