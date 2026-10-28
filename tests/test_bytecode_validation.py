"""Bytecode rejects unknown operations and invalid builtin channels."""
import unittest
from pixellang.checked_operations import RESULT_TYPES
from pixellang.model import PixelError
from pixellang.vm import validate_bytecode
from tests.bytecode_helpers import op, program


class BytecodeValidationTests(unittest.TestCase):
    def test_legacy_instructions_are_rejected(self):
        for name,arg in (('TRY',0),('END_TRY',None),('JSON_DECODE','int'),('ZLIB_DECOMPRESS','int[]')):
            with self.subTest(name=name),self.assertRaisesRegex(PixelError,'Unknown instruction'):
                validate_bytecode(program([op(name,arg),op('RETURN')]))

    def test_checked_operations_cannot_use_raw_builtin_channel(self):
        for name in (*RESULT_TYPES,'jsonDecode','fail'):
            with self.subTest(name=name),self.assertRaisesRegex(PixelError,'Unknown builtin'):
                validate_bytecode(program([op('BUILTIN',name),op('RETURN')]))

    def test_conversion_instructions_are_not_executable_bytecode(self):
        for instruction, target in (('FREEZE', 'int[]'), ('THAW', 'mut<int[]>')):
            with self.subTest(instruction=instruction):
                code = program([op('LOAD', 0), op(instruction, target), op('RETURN')])
                with self.assertRaisesRegex(PixelError, 'Unknown instruction'):
                    validate_bytecode(code)
