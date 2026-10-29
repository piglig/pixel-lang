"""Exercise the real compiler service and Studio source edits together."""
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from pixellang.bootstrap_image import HOST_STAGES
from pixellang.compiler_service import CompilerService
from pixellang.vm import VM
from pixellang.spatial_values import SpaceValue
from pixellang.workstation import Workstation
from tests.native_compiler import load_native_compiler


class NativeGraphServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = load_native_compiler(driver=None)

    def setUp(self):
        self.service = CompilerService(self.code)

    def request(self, operation, source):
        with ExitStack() as stack:
            for target in HOST_STAGES:
                stack.enter_context(patch(target, side_effect=AssertionError('Host frontend used')))
            response = self.service.request(dict(version=1, id='graph', revision=3,
                                                operation=operation, files={'main.pxl': source}))
        self.assertEqual(response['status'], 'ok', response)
        self.assertEqual(response['revision'], 3)
        return response['result']

    def test_computed_position_origin_matches_original_expression(self):
        source = """frame World = meters
effect fn main() {
 let base = Point3::[World]{x: 0.0, y: 0.0, z: 1.0}
 let offset = Vec3::[World]{x: 0.0, y: 0.0, z: 2.0}
 let moved = base + offset
 let transform = Transform3::[World, World]{rotation: [[1.0,0.0,0.0],[0.0,1.0,0.0],[0.0,0.0,1.0]], translation: Vec3::[World]{x:0.0,y:0.0,z:4.0}}
 let target = transform.point(moved)
 let world = Space::[World, string, bool]("room").insert("entity-0", target, "moving")
 print(world.size())
}
"""
        projection = self.request('native-graphs', source)['projection']
        self.assertEqual(projection['diagnostics'], [])
        computed = {source[item['range']['start']:item['range']['end']]: item
                    for item in projection['coordinates'] if not item['axes']}
        self.assertIn('base + offset', computed)
        self.assertIn('transform.point(moved)', computed)
        result = self.request('compile', source)
        vm = VM(result['bytecode'])
        origins = []
        while not vm.halted:
            vm.step(snapshot=False)
            for value in vm.stack:
                if isinstance(value, SpaceValue) and value.entities:
                    position = value.entities[0].position
                    self.assertEqual(position.xyz, (0.0, 0.0, 7.0))
                    origins.append(position.origin)
        self.assertTrue(origins)
        self.assertTrue(all(origin is not None and
            origin.node == computed['transform.point(moved)']['node']
            for origin in origins))
        self.assertEqual(vm.output, [1])

    def test_original_ast_edit_recompile_changes_execution(self):
        source = '''// 星 🛰️
fn Double(value: int) -> int = value * 2
flow Pipeline() step(value: int) -> int {
    let twice = Double(value) // keep this comment
    output twice
}
effect fn main() { print(Pipeline().step(7)) }
'''
        before = self.request('compile', source)['bytecode']
        self.assertEqual(VM(before).run(), [14])
        projection = self.request('native-graphs', source)['projection']
        self.assertEqual(projection['diagnostics'], [])
        script = '''const fs = require('fs');
const {planEdit} = require('./vscode/src/native-graph-edits');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let text = input.text;
for (const edit of planEdit(text, input.graph, input.action)) {
  text = text.slice(0, edit.start) + edit.text + text.slice(edit.end);
}
process.stdout.write(text);
'''
        edited = subprocess.run(['node', '-e', script], input=json.dumps(dict(
            text=source, graph=projection['graphs'][0],
            action=dict(kind='set-output', expression='value'))),
            text=True, capture_output=True, check=True,
            cwd=Path(__file__).resolve().parents[1]).stdout
        self.assertEqual(edited, source.replace('output twice', 'output value'))
        after = self.request('compile', edited)['bytecode']
        self.assertEqual(VM(after).run(), [7])
        self.assertEqual(self.request('native-graphs', edited)['projection']['diagnostics'], [])

    def test_incomplete_graph_is_a_successful_query_with_semantic_diagnostics(self):
        for source in (
            'fn Add(value: int) -> int = value + 1\nflow Pending() step(value: int) -> int { let add = Add(value) }\nfn main() {}',
            'flow Pending() step() -> int { let add = Missing() }\nfn main() {}',
        ):
            with self.subTest(source=source):
                projection = self.request('native-graphs', source)['projection']
                self.assertTrue(projection['diagnostics'])
                self.assertEqual(projection['graphs'][0]['name'], 'Pending')
                self.assertEqual(projection['graphs'][0]['values'][0]['name'], 'add')

    def test_syntax_error_returns_diagnostics_without_inventing_a_graph(self):
        source = 'fn Add(value) -> int = 1\nflow Pending() step() -> int { let add = Add(value) }\nfn main() {}'
        projection = self.request('native-graphs', source)['projection']
        self.assertTrue(any(item['phase'] == 'parse' for item in projection['diagnostics']))
        self.assertEqual(projection['graphs'], [])


class NativeGraphWorkstationTests(unittest.TestCase):
    def test_query_preserves_entry_cancellation_and_submission_deadline(self):
        workstation = Workstation.__new__(Workstation)
        workstation.compiler = Mock()
        projection = dict(graphs=[], diagnostics=[dict(message='Missing output')])
        workstation.compiler.request.return_value = dict(result=dict(projection=projection))
        cancelled = lambda: False
        files = {'app.pxl': 'flow Pending() step() -> int {}\nfn main() {}'}
        result = workstation.request('native-graphs', dict(files=files, entry='app.pxl'),
                                     cancelled=cancelled, submitted_at=123.0)
        self.assertEqual(result, dict(projection=projection))
        workstation.compiler.request.assert_called_once_with(
            'native-graphs', files=files, entry='app.pxl',
            cancelled=cancelled, submitted_at=123.0)
