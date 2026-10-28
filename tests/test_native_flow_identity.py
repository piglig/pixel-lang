"""A persistent child gives its parent identity even if it has no cells."""
import unittest

from tests import test_native_flow_types as flow_types


class NativeFlowIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Share the current checker driver and its content-addressed build.
        flow_types.NativeFlowTypeTests.setUpClass.__func__(cls)

    analyze = flow_types.NativeFlowTypeTests.analyze

    def test_stateless_child_still_gives_parent_persistent_identity(self):
        source = '''flow Child() step(x:int)->int {output x}
flow Parent() step(x:int)->int {instance child=Child();output child.step(x)}
fn Invalid(){let instance=Parent()}'''
        result = self.analyze(source)
        self.assertEqual([p['stateful'] for p in result['flows']], [False, True])
        self.assertTrue(all(not p['diagnostics'] for p in result['flows']), result)
        self.assertEqual(len(result['diagnostics']), 1, result)
        self.assertIn('effect', result['diagnostics'][0]['message'])


if __name__ == '__main__':
    unittest.main()
