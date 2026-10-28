from copy import deepcopy
import unittest

from pixellang.debug_artifact import prepare_debug


class DebugArtifactOwnershipTests(unittest.TestCase):
    def test_owned_and_isolated_preparation_have_identical_provenance(self):
        origin = {'source': 'main.pxl', 'start': {'offset': 0}, 'end': {'offset': 10}}
        code = {'functions': {'main': {'locals': 1, 'instructions': [
            {'op': 'PUSH', 'arg': 0, 'span': origin}]}}}
        debug = {'functions': {'main': {'source': 'main.pxl', 'names': ['value']}}}
        before = deepcopy(code)
        isolated = prepare_debug(code, debug)
        self.assertEqual(code, before)
        self.assertIsNot(isolated.bytecode, code)
        owned_code, owned_debug = deepcopy(code), deepcopy(debug)
        owned = prepare_debug(owned_code, owned_debug, take_ownership=True)
        self.assertIs(owned.bytecode, owned_code)
        self.assertIs(owned.debug, owned_debug)
        self.assertEqual(owned.inspect(), isolated.inspect())
        debug['functions']['main']['names'][0] = 'changed'
        code['functions']['main']['instructions'][0]['span']['start']['offset'] = 999
        self.assertEqual(isolated.debug['functions']['main']['names'], ['value'])
        self.assertEqual(isolated.bytecode['functions']['main']['instructions'][0]['span']['text']['start']['offset'], 0)


if __name__ == '__main__':
    unittest.main()
