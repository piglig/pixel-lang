"""Executable inout calls create, validate, transfer and release scoped accesses."""
import copy
import unittest
from pixellang.model import PixelError
from pixellang.typeexpr import parse_type, is_value_type, is_parameter_type
from pixellang.vm import VM, validate_bytecode
from tests.bytecode_helpers import program, op


class InoutBytecodeTests(unittest.TestCase):
    def code(self, dynamic=False):
        code=program([op('LOAD',0),op('RETURN')])

        code['constants']=[dict(type='int',value=1)]
        code['functions']['edit']=dict(params=['inout<int>'],locals=1,result='unit',instructions=[
            op('LOAD',0),op('PUSH',0),op('ADD'),op('STORE',0),op('PUSH',0),op('RETURN')])
        code['functions']['main']['instructions']=[op('PUSH',0),op('BIND',0)]
        if dynamic: code['functions']['main']['instructions'].append(op('FUNCTION','edit'))
        code['functions']['main']['instructions'] += [
            op('ACCESS',{'slot':0,'type':'int','path':[]}),
            op('INVOKE','fn(inout<int>)->(unit)') if dynamic else op('CALL','edit'),
            op('POP'),op('LOAD',0),op('PRINT'),op('PUSH',0),op('RETURN')]
        return code

    def test_static_and_dynamic_calls_write_back(self):
        for dynamic in (False,True):
            with self.subTest(dynamic=dynamic):
                vm=VM(self.code(dynamic))
                self.assertEqual(vm.run(),[2])
                self.assertEqual(vm.value_accesses.entries,{})

    def test_nested_call_reborrows_parameter(self):
        code=self.code()
        code['functions']['outer']=dict(params=['inout<int>'],locals=1,result='unit',instructions=[
            op('ACCESS',{'slot':0,'type':'int','path':[]}),op('CALL','edit'),op('POP'),
            op('ACCESS',{'slot':0,'type':'int','path':[]}),op('CALL','edit'),op('POP'),
            op('PUSH',0),op('RETURN')])
        for ins in code['functions']['main']['instructions']:
            if ins['op']=='CALL': ins['arg']='outer'
        vm=VM(code)
        self.assertEqual(vm.run(),[3])
        self.assertEqual(vm.value_accesses.entries,{})

    def test_pending_reborrow_allows_reading_later_value_arguments(self):
        code=self.code()
        code['functions']['add']=dict(params=['inout<int>','int'],locals=2,result='unit',instructions=[
            op('LOAD',0),op('LOAD',1),op('ADD'),op('STORE',0),op('PUSH',0),op('RETURN')])
        code['functions']['edit']['instructions']=[
            op('ACCESS',{'slot':0,'type':'int','path':[]}),op('LOAD',0),op('CALL','add'),
            op('POP'),op('PUSH',0),op('RETURN')]
        self.assertEqual(VM(code).run(),[2])

    def test_missing_or_wrong_access_contract_is_rejected(self):
        code=self.code()
        code['functions']['main']['instructions'][2]=op('LOAD',0)
        with self.assertRaises(PixelError): VM(code).run()
        code=self.code()
        code['functions']['edit']['params']=['int']
        vm=VM(code)
        with self.assertRaises(PixelError): vm.run()
        self.assertEqual(vm.value_accesses.entries,{})

    def test_mode_is_valid_only_as_function_parameter(self):
        mode=parse_type('inout<int>')
        self.assertTrue(is_parameter_type(mode))
        self.assertFalse(is_value_type(mode))
        self.assertTrue(is_value_type(parse_type('fn(inout<int>)->(int)')))
        for spelling in ('inout<int>[]','map<str,inout<int>>','fn()->(inout<int>)',
                         'fn(inout<inout<int>>)->(unit)'):
            with self.subTest(spelling=spelling):
                self.assertFalse(is_value_type(parse_type(spelling)))
        with self.assertRaises(ValueError):
            parse_type('fn(inout<mut<int[]>>)->(unit)')
        for section in ('result','captures'):
            code=self.code()
            code['functions']['edit'][section]='inout<int>' if section=='result' else ['inout<int>']
            code['functions']['edit']['locals']=2
            with self.assertRaises(PixelError): validate_bytecode(code)

    def test_invalid_access_operands_are_rejected(self):
        for operand in [None,{}, {'slot':True,'type':'int','path':[]},
                        {'slot':1,'type':'int','path':[]},
                        {'slot':0,'type':'inout<int>','path':[]},
                        {'slot':0,'type':'mut<int[]>','path':[]},
                        {'slot':0,'type':'int','path':[{'index':False}]}]:
            code=self.code()
            code['functions']['main']['instructions'][2]=op('ACCESS',operand)
            with self.subTest(operand=operand),self.assertRaises(PixelError): validate_bytecode(code)

    def test_duplicate_token_cannot_be_claimed_by_two_parameters(self):
        code=self.code()
        code['functions']['edit']['params']=['inout<int>','inout<int>']
        code['functions']['edit']['locals']=2
        code['functions']['main']['instructions'].insert(3,op('DUP'))
        vm=VM(code)
        with self.assertRaisesRegex(PixelError,'fresh scoped borrow'): vm.run()
        self.assertEqual(vm.value_accesses.entries,{})
