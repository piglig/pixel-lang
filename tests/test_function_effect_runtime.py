"""Runtime effects are declaration contracts, including unchecked bytecode."""
import copy
import unittest
from pixellang.model import PixelError
from pixellang.typeexpr import parse_type, is_assignable, is_value_type
from pixellang.vm import VM, validate_bytecode
from tests.bytecode_helpers import program, op
from tests import test_inout_bytecode as inout

class FunctionEffectRuntimeTests(unittest.TestCase):
    def code(self):
        code=program([op('PUSH',0),op('RETURN')])
        code['effectFunctions']=['main','external']
        code['functions'].pop('subject')
        code['functions']['external']=dict(params=[],locals=0,result='unit',instructions=[op('PUSH',0),op('RETURN')])
        code['functions']['pure']=copy.deepcopy(code['functions']['external'])
        return code

    def test_effect_type_roundtrip_and_direction(self):
        pure=parse_type('fn(int)->(int)')
        effect=parse_type('effect_fn(int)->(int)')
        self.assertEqual(effect.spelling(),'effect_fn(int)->(int)')
        self.assertEqual(effect.display(),'effect fn(int) -> int')
        self.assertTrue(is_value_type(effect))
        self.assertTrue(is_assignable(effect,pure))
        self.assertFalse(is_assignable(pure,effect))

    def test_empty_effectful_function_rejected_from_pure_caller(self):
        code=self.code()
        code['functions']['main']['instructions']=[op('CALL','pure'),op('RETURN')]
        code['functions']['pure']['instructions']=[op('CALL','external'),op('RETURN')]
        with self.assertRaisesRegex(PixelError,'Pure function cannot call an effect fn'):
            VM(code).run()

    def test_dynamic_call_cannot_erase_effect(self):
        for signature in ('fn()->(unit)','effect_fn()->(unit)'):
            code=self.code()
            code['functions']['main']['instructions']=[op('CALL','pure'),op('RETURN')]
            code['functions']['pure']['instructions']=[op('FUNCTION','external'),op('INVOKE',signature),op('RETURN')]
            with self.subTest(signature=signature), self.assertRaises(PixelError): VM(code).run()

    def test_effectful_caller_does_not_grant_permission_to_pure_callee(self):
        code=self.code()
        code['functions']['main']['instructions']=[op('CALL','pure'),op('RETURN')]
        code['functions']['pure']['instructions']=[op('PUSH',0),op('PRINT'),op('PUSH',0),op('RETURN')]
        vm=VM(code)
        with self.assertRaises(PixelError): vm.run()
        self.assertEqual(vm.output,[])

    def test_effectful_entry_and_pure_callback_are_allowed(self):
        code=self.code()
        code['functions']['main']['instructions']=[op('FUNCTION','pure'),op('INVOKE','effect_fn()->(unit)'),op('POP'),op('CALL','external'),op('RETURN')]
        self.assertEqual(VM(code).run(),[])

    def test_metadata_rejects_unknown_duplicate_and_mixed_contracts(self):
        for effects in (None,['missing'],['main','main'],[1]):
            code=self.code();code['effectFunctions']=effects
            with self.subTest(effects=effects), self.assertRaises(PixelError): validate_bytecode(code)
        code=self.code();code['componentFunctions']=['pure']
        with self.assertRaises(PixelError): validate_bytecode(code)

    def test_inout_local_modification_is_pure(self):
        for dynamic in (False,True):
            code=inout.InoutBytecodeTests().code(dynamic)
            code['effectFunctions']=['main']
            self.assertEqual(VM(code).run(),[2])
