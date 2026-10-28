from copy import deepcopy
import json
import unittest

from pixellang.model import PixelError
from pixellang.typesys import ERROR_FIELDS
from pixellang.vm import VM, validate_bytecode


def program():
    return {
        'magic': 'PIXELVM', 'vm_version': '0.9', 'language_version': '0.9',
        'compiler_version': 'test', 'coordinateFrames': {'@0:0': 'meters'},
        'records': {'Error': dict(ERROR_FIELDS)}, 'enums': {},
        'constants': [{'type': 'float64', 'value': value} for value in (1.0, 2.0, 3.0)],
        'effectFunctions': ['main'], 'entry': ['main'], 'functions': {'main': {
            'params': [], 'captures': [], 'locals': 0, 'result': 'unit',
            'instructions': [
                {'op': 'PUSH', 'arg': 0}, {'op': 'PUSH', 'arg': 1}, {'op': 'PUSH', 'arg': 2},
                {'op': 'SPATIAL_VALUE', 'arg': {'type': 'Point3<@0:0>', 'fields': ['x', 'y', 'z']}},
                {'op': 'DUP', 'arg': None}, {'op': 'PRINT', 'arg': None},
                {'op': 'RETURN', 'arg': None},
            ],
        }},
    }


class NativeSpatialVMTests(unittest.TestCase):
    def test_effect_caller_does_not_grant_space_creation_to_a_pure_function(self):
        code = program()
        code['constants'] = [{'type': 'str', 'value': 'world'}]
        code['functions']['create'] = dict(params=[], locals=0, result='Space<@0:0,int,str>',
            instructions=[{'op': 'PUSH', 'arg': 0},
                          {'op': 'SPACE_NEW', 'arg': 'Space<@0:0,int,str>'},
                          {'op': 'RETURN'}])
        code['functions']['main']['instructions'] = [{'op': 'CALL', 'arg': 'create'}, {'op': 'RETURN'}]
        vm = VM(code)
        with self.assertRaisesRegex(PixelError, 'Runtime identity effects are forbidden'):
            vm.run()
        self.assertEqual(vm.space_identities, set())

    def test_native_value_runs_and_materializes(self):
        self.assertEqual(VM(program()).run(), [{'x': 1.0, 'y': 2.0, 'z': 3.0}])
        traced = VM(program(), trace=True)
        self.assertEqual(traced.run(), [{'x': 1.0, 'y': 2.0, 'z': 3.0}])
        self.assertTrue(all(event['function'] == 'main' for event in traced.trace))

    def test_snapshot_retains_type_frame_and_unit(self):
        vm = VM(program())
        for _ in range(4): vm.step(snapshot=False)
        self.assertEqual(vm.snapshot_value(vm.stack[-1]), {
            'type': 'Point3<@0:0>', 'unit': 'meters', 'coordinates': [1.0, 2.0, 3.0],
        })

    def test_untrusted_bytecode_rejects_undeclared_frames_and_bad_fields(self):
        base = program()
        for changed in [
            {'type': 'Point3<@0:1>', 'fields': ['x', 'y', 'z']},
            {'type': 'Point3<@0:0>', 'fields': ['x', 'x', 'z']},
            {'type': 'Point3<@0:0>', 'fields': ['x', [], 'z']},
            {'type': 'Point3<int>', 'fields': ['x', 'y', 'z']},
            {'type': 'Vec3<@0:0>[]', 'fields': ['x', 'y', 'z']},
        ]:
            code = deepcopy(base)
            code['functions']['main']['instructions'][3]['arg'] = changed
            with self.subTest(changed=changed), self.assertRaises(PixelError):
                validate_bytecode(code)
        for unit in ('pixels', {}, None):
            code = deepcopy(base)
            code['coordinateFrames']['@0:0'] = unit
            with self.subTest(unit=unit), self.assertRaises(PixelError):
                validate_bytecode(code)

    def test_runtime_still_checks_malformed_operand_values(self):
        code = program()
        code['constants'][0] = {'type': 'int', 'value': 1}
        with self.assertRaises(PixelError): VM(code).run()

    def test_checkpoint_restores_space_identities_and_allocation_budget(self):
        code = program()
        code['constants'] = [{'type': 'str', 'value': 'world'}]
        code['functions']['main']['instructions'] = [
            {'op': 'PUSH', 'arg': 0}, {'op': 'SPACE_NEW', 'arg': 'Space<@0:0,int,str>'},
            {'op': 'DUP', 'arg': None}, {'op': 'PRINT', 'arg': None}, {'op': 'RETURN', 'arg': None},
        ]
        vm = VM(code)
        before = vm.checkpoint()
        expected = vm.run()
        final = vm.checkpoint()
        self.assertEqual(vm.space_identities, {'world'})
        vm.restore_checkpoint(before)
        self.assertEqual(vm.space_identities, set())
        self.assertEqual(vm.spatial_items, 0)
        self.assertEqual(vm.run(), expected)
        self.assertEqual(vm.checkpoint(), final)

    def test_checkpoint_budget_counts_native_payloads(self):
        from pixellang.recording import footprint
        from pixellang.spatial_values import CoordinateFrame, FrozenValue, SpaceValue, SpatialValue
        frame = CoordinateFrame('@0:0', 'meters')
        position = SpatialValue('Point3', frame, (0.0, 0.0, 0.0))
        empty = SpaceValue('world', frame, 'int[]', 'str')
        values = tuple(range(1000))
        populated = empty.insert(position, FrozenValue('int[]', values))
        self.assertGreater(footprint(populated), footprint(empty) + 8000)

    def test_transform_json_round_trip_preserves_matrix_and_translation(self):
        code = program()
        code['coordinateFrames']['@0:1'] = 'centimeters'
        data = {'rotation': [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                'translation': {'x': 10.0, 'y': 0.0, 'z': 0.0}}
        code['constants'] = [{'type': 'str', 'value': json.dumps(data)}]
        result = 'Result<Transform3<@0:1,@0:0>,Error>'
        code['enums'][result] = {'Ok': {'value': 'Transform3<@0:1,@0:0>'}, 'Err': {'error': 'Error'}}
        code['functions']['main']['instructions'] = [
            {'op': 'PUSH', 'arg': 0}, {'op': 'RESULT_DECODE', 'arg': {'result': result}},
            {'op': 'ENUM_GET', 'arg': {'type': result, 'variant': 'Ok', 'field': 'value'}},
            {'op': 'DUP', 'arg': None}, {'op': 'BUILTIN', 'arg': 'jsonFrom'},
            {'op': 'PRINT', 'arg': None}, {'op': 'RETURN', 'arg': None},
        ]
        self.assertEqual(json.loads(VM(code).run()[0]), data)
        data['rotation'][0][0] = 9.0
        code['constants'][0]['value'] = json.dumps(data)
        code['functions']['main']['instructions'] = [
            {'op': 'PUSH', 'arg': 0}, {'op': 'RESULT_DECODE', 'arg': {'result': result}},
            {'op': 'DUP', 'arg': None}, {'op': 'PRINT', 'arg': None}, {'op': 'RETURN', 'arg': None},
        ]
        failure = VM(code).run()[0]
        self.assertEqual(failure['variant'], 'Err')
        self.assertEqual(failure['fields']['error']['operation'], 'jsonDecode')
