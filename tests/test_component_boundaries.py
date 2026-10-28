"""Removed component representations must fail instead of selecting legacy behavior."""
import unittest
from pixellang.model import PixelError
from pixellang.typeexpr import parse_type, Type, is_value_type
from pixellang.vm import VM,validate_bytecode
from tests.bytecode_helpers import program,op

class RemovedComponentTests(unittest.TestCase):
    def test_legacy_metadata_rejected_even_when_empty(self):
        for names in ([],['subject']):
            code=program([op('LOAD',0),op('RETURN')])
            code['componentFunctions']=names
            with self.assertRaisesRegex(PixelError,'Legacy component metadata'):
                validate_bytecode(code)

    def test_effect_metadata_is_required(self):
        code=program([op('LOAD',0),op('RETURN')])
        del code['effectFunctions']
        with self.assertRaisesRegex(PixelError,'missing effect'):
            VM(code)

    def test_component_function_type_removed(self):
        with self.assertRaises(ValueError):parse_type('component(int)->(int)')
        self.assertFalse(is_value_type(Type('function','component',(parse_type('int'),))))

    def test_legacy_type_cannot_be_rendered_as_plain_fn(self):
        invalid=Type('function','component',(parse_type('int'),))
        for render in (invalid.spelling,invalid.display):
            with self.assertRaises(ValueError):render()

    def test_stream_header_requires_new_contract(self):
        import json
        from pixellang.bootstrap import read_bytecode_stream
        header=dict(kind='header',protocol=2,diagnostics=[],magic='PIXELVM',
                    vm_version='0.9',language_version='0.9',compiler_version='test',entry=['main'])
        for fields in ({},{'componentFunctions':[]},{'effectFunctions':None}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                read_bytecode_stream([json.dumps({**header,**fields})])
