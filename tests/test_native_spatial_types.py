"""Native spatial type identities resolved by the self-hosted frontend."""
import json
from pathlib import Path
import unittest

from pixellang.compiler_client import CompilerClient
from pixellang.vm import VM

ROOT = Path(__file__).resolve().parents[1]


class NativeSpatialTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = {name: (ROOT / 'selfhost' / name).read_text() for name in (
            'model.pxl', 'lexer.pxl', 'parser.pxl', 'project.pxl', 'bindings.pxl', 'types.pxl')}
        files['main.pxl'] = '''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
effect fn main() {
 match jsonDecode::[map[string, string]](input()) {
  .Ok(files) => { Dispatch(files) },
  .Err(error) => { print(error.message) }
 }
}
effect fn Dispatch(files:map[string, string]) {
 print(jsonFrom(types.Analyze(bindings.Bind(project.Build(files, "main.pxl")))))
}
'''
        with CompilerClient() as client:
            cls.code = client.request('compile', files=files)['result']['bytecode']

    def analyze(self, source, **modules):
        files = {'main.pxl': source, **modules}
        return json.loads(VM(self.code, input_text=json.dumps(files),
                             max_steps=20_000_000).run()[0])

    def test_position_displacement_and_frames_have_distinct_identities(self):
        result = self.analyze('''frame World = meters
frame Cabin = meters
fn example(a: Point3[World], b: Vec3[World], c: Point3[Cabin],
 t: Transform3[Cabin, World], entities: Space[World, int,string]) {}
''')
        self.assertEqual(result['diagnostics'], [])
        types = result['types']
        params = types[result['signatures'][0]['typeId']]['arguments'][:-1]
        self.assertEqual(len(set(params)), 5)
        self.assertEqual([types[i]['kind'] for i in params], ['spatial'] * 5)
        self.assertNotEqual(types[params[0]]['arguments'], types[params[2]]['arguments'])
        self.assertEqual(types[params[0]]['arguments'], types[params[1]]['arguments'])

    def test_frame_arity_units_and_payload_are_checked(self):
        for source, message in [
            ('frame W = pixels', 'Unknown coordinate frame unit'),
            ('fn f(x: Point3[int]) {}', 'declared coordinate frame'),
            ('frame W = meters\nfn f(x: Transform3[W, int]) {}', 'declared coordinate frame'),
            ('frame W = meters\nfn f(x: Point3[W, W]) {}', 'Wrong spatial type argument count'),
            ('frame W = meters\nfn f(x: Entity[W, W]) {}', 'payload requires a value type'),
        ]:
            with self.subTest(source=source):
                diagnostics = self.analyze(source)['diagnostics']
                self.assertTrue(diagnostics)
                self.assertIn(message, diagnostics[0]['message'])
                self.assertEqual(diagnostics[0]['source'], 'main.pxl')

    def test_imported_frame_identity_and_visibility(self):
        source = '''import "coordinates.pxl" as coords
fn f(a: Point3[coords.World], b: Vec3[coords.World]) {}
'''
        valid = self.analyze(source, **{'coordinates.pxl': 'export frame World = meters'})
        self.assertEqual(valid['diagnostics'], [])
        invalid = self.analyze(source, **{'coordinates.pxl': 'frame World = meters'})
        self.assertIn('Type is private', invalid['diagnostics'][0]['message'])


if __name__ == '__main__':
    unittest.main()
