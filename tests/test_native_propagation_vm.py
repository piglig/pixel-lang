"""Native propagation returns typed failures, releasing only the current frame."""
from copy import deepcopy
import unittest

from pixellang.model import PixelError
from pixellang.typeexpr import parse_type, is_value_type
from pixellang.vm import VM, validate_bytecode
from tests.bytecode_helpers import op, program


def schema(typ):
    parsed=parse_type(typ)
    if parsed.name=='Option':return {'Some':{'value':parsed.args[0].spelling()},'None':{}}
    return {'Ok':{'value':parsed.args[0].spelling()},'Err':{'error':parsed.args[1].spelling()}}


def fixture(failed=True, optional=False, prefix=False):
    source='Option<int>' if optional else 'Result<int,str>'
    target='Option<str>' if optional else 'Result<str,str>'
    code=program([])
    code['constants']=[dict(type='int',value=99),dict(type='str',value='success'),dict(type='str',value='failure')]
    code['enums']={typ:schema(typ) for typ in (source,target)}
    tag=('None' if failed else 'Some') if optional else ('Err' if failed else 'Ok')
    fields=[] if optional and failed else ['error' if failed else 'value']
    construct=[] if not fields else [op('PUSH',2 if failed else 0)]
    construct += [op('ENUM',dict(type=source,variant=tag,fields=fields))]
    body=[op('PUSH',0)] if prefix else []
    body += [op('LOAD',0),op('PROPAGATE',dict(source=source,target=target)),op('POP')]
    if prefix:body.append(op('POP'))
    body += [op('PUSH',1),op('ENUM',dict(type=target,variant='Some' if optional else 'Ok',fields=['value'])),op('RETURN')]
    code['functions']['subject']=dict(params=[source],locals=1,result=target,instructions=body)
    code['functions']['main']['instructions']=[op('PUSH',0)]+construct+[
        op('CALL','subject'),op('PRINT'),op('PRINT'),op('PUSH',0),op('RETURN')]
    return code


class NativePropagationVMTests(unittest.TestCase):
    def test_result_success_and_error_retargeting_with_pending_operands(self):
        for failed in (True,False):
            for prefix in (True,False):
                with self.subTest(failed=failed,prefix=prefix):
                    vm=VM(fixture(failed=failed,prefix=prefix))
                    self.assertEqual(vm.run(),[{'variant':'Err' if failed else 'Ok','fields':{'error' if failed else 'value':'failure' if failed else 'success'}},99])
                    self.assertEqual(vm.value_accesses.entries,{})

    def test_option_propagates_none_across_different_payload_types(self):
        for failed in (True,False):
            with self.subTest(failed=failed):
                output=VM(fixture(failed=failed,optional=True,prefix=True)).run()
                self.assertEqual(output,[{'variant':'None' if failed else 'Some','fields':{} if failed else {'value':'success'}},99])

    def test_checkpoint_before_propagation_replays_identically(self):
        vm=VM(fixture(prefix=True))
        while True:
            frame=vm.call_stack[-1]
            if vm.bytecode['functions'][frame.function]['instructions'][frame.pc]['op']=='PROPAGATE':break
            vm.step(snapshot=False)
        checkpoint=vm.checkpoint()
        expected=vm.run();finished=vm.checkpoint()
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(),expected)
        self.assertEqual(vm.checkpoint(),finished)

    def test_early_return_releases_pending_reborrow_without_undoing_prior_write(self):
        code=fixture()
        subject=code['functions']['subject']
        subject.update(params=['inout<int>','Result<int,str>'],locals=2)
        subject['instructions']=[op('PUSH',0),op('STORE',0),
            op('ACCESS',dict(slot=0,type='int',path=[])),op('LOAD',1),
            op('PROPAGATE',dict(source='Result<int,str>',target='Result<str,str>')),
            op('POP'),op('POP'),op('PUSH',1),op('ENUM',dict(type='Result<str,str>',variant='Ok',fields=['value'])),op('RETURN')]
        code['constants'].append(dict(type='int',value=1))
        code['functions']['main']['instructions']=[op('PUSH',3),op('BIND',0),
            op('ACCESS',dict(slot=0,type='int',path=[])),op('PUSH',2),op('ENUM',dict(type='Result<int,str>',variant='Err',fields=['error'])),
            op('CALL','subject'),op('PRINT'),op('LOAD',0),op('PRINT'),
            op('ACCESS',dict(slot=0,type='int',path=[])),op('POP'),op('PUSH',0),op('RETURN')]
        vm=VM(code)
        self.assertEqual(vm.run(),[{'variant':'Err','fields':{'error':'failure'}},99])
        self.assertEqual(vm.value_accesses.entries,{})

    def test_forged_native_result_schemas_and_arity_are_rejected(self):
        self.assertTrue(is_value_type(parse_type('Result<int,str>')))
        for spelling in ('Result','Result<int>','Result<int,str,bool>','Result<unit,str>'):
            self.assertFalse(is_value_type(parse_type(spelling)),spelling)
        for variants in ({'Ok':{'value':'int'}}, {'Ok':{'value':'str'},'Err':{'error':'str'}}, {'Ok':{'value':'int'},'Err':{'wrong':'str'}}):
            code=fixture();code['enums']['Result<int,str>']=variants
            with self.assertRaisesRegex(PixelError,'Invalid Result schema'):validate_bytecode(code)
        code=fixture();code['records']['Result<int,str>']={}
        with self.assertRaises(PixelError):validate_bytecode(code)

    def test_propagation_cannot_cross_error_types_or_use_user_enum_shape(self):
        for target in ('Result<str,int>','Option<str>','@0:1'):
            code=fixture()
            code['enums'][target]=schema(target) if target!='@0:1' else {'Ok':{'value':'str'},'Err':{'error':'str'}}
            code['functions']['subject']['instructions'][1]['arg']['target']=target
            code['functions']['subject']['result']=target
            with self.subTest(target=target),self.assertRaises(PixelError):validate_bytecode(code)
        code=fixture();code['functions']['subject']['result']='Result<int,str>'
        with self.assertRaisesRegex(PixelError,'enclosing function'):validate_bytecode(code)

    def test_empty_current_frame_cannot_read_caller_operands(self):
        code=fixture();code['functions']['subject']['instructions'].pop(0)
        with self.assertRaisesRegex(PixelError,'stack underflow'):VM(code).run()


if __name__=='__main__':unittest.main()
