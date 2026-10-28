"""Result-returning operation boundaries, effects, replay and fatal failures."""
from pathlib import Path
import tempfile
import unittest

from pixellang.fileaccess import FileAccess
from pixellang.model import PixelError
from pixellang.vm import VM, validate_bytecode
from tests.bytecode_helpers import op, program


def fixture(name='parseInt', values=('42',), success='int', *, decode=False):
    code=program([])
    code['functions'].pop('subject')
    result=f'Result<{success},Error>'
    code['enums']={result:{'Ok':{'value':success},'Err':{'error':'Error'}}}
    code['constants']=[dict(type='bool' if type(v) is bool else 'int' if type(v) is int else 'str',value=v) for v in values]
    code['constants'].append(dict(type='int',value=0))
    body=[op('PUSH',i) for i in range(len(values))]
    body += [op('RESULT_DECODE',dict(result=result)) if decode else op('RESULT_BUILTIN',dict(name=name,result=result)),
             op('PRINT'),op('PUSH',len(values)),op('RETURN')]
    code['functions']['main']['instructions']=body
    return code


class CheckedOperationVMTests(unittest.TestCase):
    def compression_fixture(self, values):
        code=fixture('zlibCompress',values,'int[]')
        code['functions']['main']['instructions'].insert(len(values),op('ARRAY',dict(count=len(values),type='int[]')))
        return code

    def test_checked_compression_roundtrip_and_replay(self):
        import zlib
        vm=VM(self.compression_fixture((0,65,255)))
        before=vm.checkpoint();output=vm.run()
        self.assertEqual(output[0]['variant'],'Ok')
        self.assertEqual(zlib.decompress(bytes(output[0]['fields']['value'])),bytes([0,65,255]))
        vm.restore_checkpoint(before)
        self.assertEqual(vm.run(),output)

    def test_checked_compression_bad_bytes_are_results(self):
        for bad in (-1,256):
            with self.subTest(bad=bad):
                output=VM(self.compression_fixture((65,bad))).run()
                self.assertEqual(output[0]['variant'],'Err')
                self.assertEqual(output[0]['fields']['error']['code'],'bytes.range')
                self.assertEqual(output[0]['fields']['error']['operation'],'zlibCompress')

    def test_checked_compression_keeps_resource_failures_fatal(self):
        vm=VM(self.compression_fixture((65,)),max_heap_items=2)
        with self.assertRaisesRegex(PixelError,'budget'):vm.run()
        self.assertEqual(vm.output,[])

    def test_unchecked_compression_bytecode_is_rejected(self):
        code=self.compression_fixture((65,))
        code['functions']['main']['instructions'][2]=op('BUILTIN','zlibCompress')
        with self.assertRaises(PixelError):validate_bytecode(code)

    def test_parsing_success_and_structured_failure(self):
        self.assertEqual(VM(fixture()).run(),[{'variant':'Ok','fields':{'value':42}}])
        error=VM(fixture(values=('no',))).run()[0]
        self.assertEqual(error['variant'],'Err')
        self.assertEqual(error['fields']['error']['code'],'numeric.invalid_text')
        self.assertEqual(error['fields']['error']['operation'],'parseInt')
        self.assertEqual(VM(fixture('parseFloat64',('1.5',),'float64')).run()[0]['fields']['value'],1.5)

    def test_typed_json_decode_handles_nested_values_and_schema_errors(self):
        code=fixture(values=('{"items":[1,2]}',),success='@0:1',decode=True)
        code['records']['@0:1']={'items':'int[]'}
        self.assertEqual(VM(code).run(),[{'variant':'Ok','fields':{'value':{'items':[1,2]}}}])
        code['constants'][0]['value']='{"items":[1,"bad"]}'
        error=VM(code).run()[0]['fields']['error']
        self.assertEqual(error['code'],'json.type_mismatch')
        self.assertEqual(error['path'],'$.items[1]')
        self.assertEqual(error['operation'],'jsonDecode')

    def test_data_failure_is_a_normal_result_and_execution_continues(self):
        code=fixture(values=('bad',))
        body=code['functions']['main']['instructions']
        body[-1:-1]=[op('PRINT'), op('PUSH',1)]
        output=VM(code).run()
        self.assertEqual(output[0]['variant'],'Err')
        self.assertEqual(output[1],0)

    def test_io_failures_are_recorded_and_replayed_as_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            access=FileAccess(root)
            code=fixture('readText',('missing.txt',),'str')
            vm=VM(code,file_access=access)
            expected=vm.run()
            self.assertEqual(expected[0]['fields']['error']['code'],'io.not_found')
            self.assertEqual(vm.effect_cursor,1)
            (root/'missing.txt').write_text('appeared later')
            replay=FileAccess.restore(access.document())
            self.assertEqual(VM(code,file_access=replay).run(),expected)

    def test_pure_functions_cannot_hide_io_in_result_operations(self):
        code=fixture('readText',('anything',),'str')
        code['effectFunctions']=[]
        vm=VM(code)
        with self.assertRaisesRegex(PixelError,'External effects'):vm.run()
        self.assertEqual(vm.effect_cursor,0)

    def test_successful_write_is_not_repeated_after_checkpoint_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            vm=VM(fixture('writeText',('out.txt','first'),'bool'),file_access=FileAccess(root,root))
            while vm.call_stack[-1].pc<2:vm.step(snapshot=False)
            before=vm.checkpoint()
            expected=vm.run()
            self.assertEqual(expected,[{'variant':'Ok','fields':{'value':True}}])
            (root/'out.txt').write_text('external change')
            vm.restore_checkpoint(before)
            self.assertEqual(vm.run(),expected)
            self.assertEqual((root/'out.txt').read_text(),'external change')

    def test_resource_exhaustion_and_cancellation_are_not_error_values(self):
        vm=VM(fixture(),max_heap_items=1)
        with self.assertRaisesRegex(PixelError,'budget'):vm.run()
        self.assertEqual(vm.output,[])
        vm=VM(fixture());vm.cancel()
        with self.assertRaisesRegex(PixelError,'cancelled'):vm.run()
        self.assertEqual(vm.output,[])

    def test_result_allocation_failure_after_write_remains_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            vm=VM(fixture('writeText',('out.txt','written'),'bool'),file_access=FileAccess(root,root),max_heap_items=1)
            with self.assertRaisesRegex(PixelError,'budget'):vm.run()
            self.assertEqual((root/'out.txt').read_text(),'written')
            self.assertEqual(vm.effect_cursor,1)
            self.assertEqual(vm.output,[])

    def test_forged_result_types_and_unlisted_operations_are_rejected(self):
        for name,success,error in [('parseInt','str','Error'),('assert','int','Error'),('parseInt','int','str')]:
            code=fixture(name,success=success)
            result=f'Result<{success},{error}>'
            code['enums']={result:{'Ok':{'value':success},'Err':{'error':error}}}
            code['functions']['main']['instructions'][1]['arg']['result']=result
            with self.subTest(name=name,success=success,error=error),self.assertRaises(PixelError):validate_bytecode(code)

    def test_operand_underflow_does_not_consume_caller_stack(self):
        code=fixture()
        code['functions']['subject']=dict(params=[],locals=0,result='Result<int,Error>',instructions=[
            op('RESULT_BUILTIN',dict(name='parseInt',result='Result<int,Error>')),op('RETURN')])
        code['functions']['main']['instructions']=[op('PUSH',1),op('CALL','subject'),
            op('POP'),op('POP'),op('PUSH',1),op('RETURN')]
        vm=VM(code)
        with self.assertRaisesRegex(PixelError,'underflow'):vm.run()
        self.assertEqual(vm.stack,[0])


if __name__=='__main__':unittest.main()
