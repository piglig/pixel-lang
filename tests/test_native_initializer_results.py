"""The migrated native frontend diagnoses expected initializer failures as values."""
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from pixellang.checked_operations import RESULT_TYPES
from pixellang.vm import VM
from tests.native_compiler import compile_native_sources
from tests.selfhost_runtime import SELFHOST_HEAP_LIMITS

ROOT=Path(__file__).resolve().parents[1]
DRIVER='''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
effect fn main(){
 match jsonDecode::[map[string,string]](input()) {
  .Ok(files)=>{
   let checked=checker.Check(types.Analyze(bindings.Bind(project.Build(files,"main.pxl"))))
   print(checked.diagnostics)
   print(checked.flows)
  },
  .Err(error)=>{print(error.code)}
 }
}'''


class NativeInitializerResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files={'main.pxl':DRIVER};pending=re.findall(r'import "([^"]+)"',DRIVER)
        while pending:
            name=pending.pop()
            if name in files:continue
            files[name]=(ROOT/'selfhost'/name).read_text()
            pending.extend(re.findall(r'import "([^"]+)"',files[name]))
        cls.code=compile_native_sources(files)

    def check(self,source):
        with patch('pixellang.text.parse_text',side_effect=AssertionError('host parser is forbidden')):
            outputs=VM(self.code,input_text=json.dumps({'main.pxl':source}),max_steps=40_000_000,**SELFHOST_HEAP_LIMITS).run()
        return dict(diagnostics=outputs[0],flows=outputs[1])

    def test_frontend_has_no_legacy_recoverable_execution_paths(self):
        instructions=[i for f in self.code['functions'].values() for i in f['instructions']]
        self.assertFalse(any(i['op'] in ('TRY','END_TRY','JSON_DECODE','ZLIB_DECOMPRESS') for i in instructions))
        self.assertFalse(any(i['op']=='BUILTIN' and i['arg'] in {*RESULT_TYPES,'fail'} for i in instructions))
        self.assertTrue(any(i['op']=='RESULT_DECODE' for i in instructions))

    def test_retired_fail_builtin_is_not_available(self):
        result = self.check('effect fn main(){fail("bad")}')
        self.assertTrue(result['diagnostics'])
        self.assertTrue(any('fail' in d['message'] for d in result['diagnostics']), result)

    def test_user_fail_function_does_not_imply_return(self):
        source = 'fn fail()->int=1\nfn incomplete()->int{let ignored=fail()}\nfn main(){}'
        self.assertTrue(self.check(source)['diagnostics'])
        valid = 'fn fail(value:int)->int=value\nfn value()->int{return fail(1)}\nfn main(){let try=value();let catch=try}'
        self.assertEqual(self.check(valid)['diagnostics'], [])

    def test_nested_failure_retains_source_location(self):
        result=self.check('''flow Broken() step()->[int]{
 state values:[int]=[1,
     2/0]
 next values=values
 output values
}
fn main(){}''')
        errors=[d for d in result['diagnostics'] if 'Invalid state initializer' in d['message']]
        self.assertEqual(len(errors),1,result)
        self.assertEqual(errors[0]['line'],3)
        self.assertIn('Division by zero',errors[0]['message'])
        self.assertEqual(result['flows'][0]['initializers'],{})

    def test_short_circuit_and_recursive_enum_equality(self):
        result=self.check('''enum Box {Value(values:[int])}
flow Stored() step()->bool{
 state safe=false && (1/0==0)
 state equal=Box.Value([1,2])==Box.Value([1,2])
 next safe=safe
 next equal=equal
 output equal
}
fn main(){}''')
        self.assertEqual(result['diagnostics'],[])
        self.assertEqual([json.loads(v['value']) for v in result['flows'][0]['initializers'].values()],[False,True])

    def test_overflow_and_duplicate_keys_are_diagnostics(self):
        for expression,typ,message in [
            ('9223372036854775807+1','int','Integer overflow'),
            ('1.7976931348623157e308*2.0','float64','Float64 overflow'),
            ('map[string,int]{"x":1,"x":2}','map[string,int]','Duplicate map key'),
        ]:
            with self.subTest(expression=expression):
                result=self.check('flow Broken() step()->'+typ+'{state value='+expression+'\nnext value=value\noutput value}\nfn main(){}')
                self.assertTrue(any(message in d['message'] for d in result['diagnostics']),result)
                self.assertEqual(result['flows'][0]['initializers'],{})

    def test_invalid_rotation_becomes_initializer_diagnostic(self):
        result=self.check('''frame World=meters
flow Broken() step()->Transform3[World,World]{
 state transform=Transform3::[World,World]{rotation:[[1.0,0.0,0.0],[0.0,1.0,0.0],[0.0,0.0,-1.0]],translation:Vec3::[World]{x:0.0,y:0.0,z:0.0}}
 next transform=transform
 output transform
}
fn main(){}''')
        self.assertTrue(any('preserve handedness' in d['message'] for d in result['diagnostics']),result)
        self.assertEqual(result['flows'][0]['initializers'],{})


if __name__=='__main__':unittest.main()
