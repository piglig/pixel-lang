"""Executable image isolation and reuse, including hostile mutable inputs."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from pixellang.code_image import CodeImage
from pixellang.model import PixelError
from pixellang.typesys import ERROR_FIELDS
from pixellang.vm import VM


def program():
    span = dict(source='image.png', points=[[1, 2], [2, 2]], min=[1, 2], max=[2, 2])
    return dict(magic='PIXELVM', vm_version='0.9', language_version='0.9',
                compiler_version='test', records={'Error': dict(ERROR_FIELDS)}, enums={}, effectFunctions=['main'],
                constants=[dict(type='int', value=42), dict(type='int', value=0)], entry=['main'], functions={
                    'main': dict(params=[], captures=[], locals=0, result='unit', instructions=[
                        dict(op='PUSH', arg=0, span=span), dict(op='PRINT', span=span),
                        dict(op='PUSH', arg=1), dict(op='RETURN')])})


class CodeImageTests(unittest.TestCase):
    def test_original_and_each_vm_state_are_isolated(self):
        code = program()
        image = CodeImage(code)
        first, second = VM(image), VM(image)
        self.assertIs(first.bytecode, second.bytecode)
        self.assertIs(first.instructions, second.instructions)
        self.assertIs(first.code_image.effect_functions, second.code_image.effect_functions)
        self.assertEqual(image.effect_functions, frozenset({'main'}))
        code['effectFunctions'].clear()
        self.assertEqual(image.effect_functions, frozenset({'main'}))
        code['constants'][0]['value'] = 99
        code['functions']['main']['instructions'][0]['span']['points'][0][0] = 99
        first.step(snapshot=False)
        self.assertEqual(first.stack, [42])
        self.assertEqual(second.stack, [])
        self.assertEqual(first.run(), [42])
        self.assertEqual(second.run(), [42])
        self.assertEqual(first.bytecode['functions']['main']['instructions'][0]['span']['points'][0], [1, 2])
        self.assertIs(first.bytecode['functions']['main']['instructions'][0]['span'],
                      first.bytecode['functions']['main']['instructions'][1]['span'])

    def test_nested_mutation_and_reinitialization_are_rejected(self):
        image = CodeImage(program())
        root, array = image.data, image.data['entry']
        for operation in [lambda: root.update(magic='bad'), lambda: root.clear(),
                          lambda: root.__setitem__('magic', 'bad'), lambda: root.__init__({}),
                          lambda: root.pop('magic'), lambda: root.setdefault('extra', 1),
                          lambda: array.append('bad'), lambda: array.__setitem__(0, 'bad'),
                          lambda: array.__init__([]), lambda: array.reverse(),
                          lambda: array.__iadd__(['bad']), lambda: array.__imul__(2),
                          lambda: image.__init__(program()), lambda: setattr(image, '_data', {})]:
            with self.subTest(operation=operation), self.assertRaises(TypeError): operation()
        self.assertEqual(VM(image).run(), [42])
        self.assertEqual(json.loads(json.dumps(image.data)), program())
        self.assertIs(deepcopy(image), image)

    def test_decoded_execution_plan_is_immutable_and_preserves_budget_location(self):
        image = CodeImage(program())
        with self.assertRaises(TypeError):
            image.instructions['main'] = ()
        with self.assertRaises(TypeError):
            image.instructions['main'][0][2]['points'][0][0] = 9
        vm = VM(image, max_steps=1)
        with self.assertRaisesRegex(PixelError, 'budget'):
            vm.run()
        self.assertEqual(vm.steps, 1)
        self.assertEqual(vm.error['span']['points'], [[1, 2], [2, 2]])
        self.assertEqual(vm.output, [])

    def test_prepared_image_does_not_repeat_validation(self):
        image = CodeImage(program())
        with patch('pixellang.vm.validate_bytecode', side_effect=AssertionError('Repeated validation')):
            self.assertEqual(VM(image).run(), [42])
            self.assertEqual(VM(image).run(), [42])
        malformed = program()
        malformed['functions']['main']['instructions'][0]['op'] = 'UNTRUSTED'
        with self.assertRaises(PixelError): VM(malformed)
        with self.assertRaises(PixelError): CodeImage(malformed)

    def test_cycles_non_json_and_excessive_nesting_are_rejected(self):
        for extra in [object(), float('nan'), {1: 'bad'}]:
            code = program(); code['extra'] = extra
            with self.subTest(extra=extra), self.assertRaises(PixelError): CodeImage(code)
        code = program(); code['extra'] = code
        with self.assertRaisesRegex(PixelError, 'cycles'): CodeImage(code)
        code = program(); nested = code
        for _ in range(130): nested['extra'] = {}; nested = nested['extra']
        with self.assertRaisesRegex(PixelError, 'nesting'): CodeImage(code)

    def test_raw_input_is_still_detached_and_trace_is_preserved(self):
        code = program(); vm = VM(code, trace=True)
        code['constants'][0]['value'] = -1
        self.assertEqual(vm.run(), [42])
        self.assertEqual(vm.trace[0]['span']['points'], [[1, 2], [2, 2]])
