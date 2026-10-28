"""Studio graph projections come from the PixelLang AST, including incomplete graphs."""
from tests.native_compiler import native_service
import json
import subprocess
from pathlib import Path
import unittest

from pixellang.vm import VM
from pixellang.spatial_values import SpaceValue


class NativeGraphProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = native_service()
        cls.addClassCleanup(cls.service.close)

    def project(self, source, **modules):
        response = self.service.request(dict(version=1, operation='native-graphs',
                                             files={'main.pxl': source, **modules}))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']['projection']

    def test_imported_expressions_spatial_types_and_original_unicode_ranges(self):
        source = '''// 星 🛰️
import "lib.pxl" as lib
frame World = meters
flow Pipeline() step(values: [int]) -> int {
 let total = lib.Total(values)
 output total
}
effect fn main() { print(Pipeline().step([1, 2])) }
'''
        projection = self.project(source, **{'lib.pxl': '''export fn Total(values: [int]) -> int {
 var result = 0
 for value in values { result += value }
 return result
}
fn Private(value: int) -> int = value
export fn ordinary(value: int) -> int = value
export effect fn report(value: int) { print(value) }
'''})
        self.assertEqual(projection['diagnostics'], [])
        self.assertEqual(projection['version'], 'pixellang-native-graphs/2')
        graph = projection['graphs'][0]
        self.assertEqual(graph['source'], 'main.pxl')
        self.assertEqual(source[graph['range']['start']:graph['range']['end']].splitlines()[0], 'flow Pipeline() step(values: [int]) -> int {')
        self.assertEqual(source[graph['body']['start']], '{')
        self.assertEqual(source[graph['body']['end'] - 1], '}')
        value = graph['values'][0]
        self.assertEqual(value['type'], 'int')
        self.assertEqual(value['expression']['type'], 'int')
        self.assertEqual(source[value['range']['start']:value['range']['end']], 'let total = lib.Total(values)')
        edge = graph['dependencies'][0]
        self.assertEqual(edge['source'], graph['inputs'][0]['node'])
        self.assertEqual(edge['target'], value['node'])
        self.assertEqual(source[edge['range']['start']:edge['range']['end']], 'values')
        targets = [item['target'] for item in graph['palette']]
        self.assertIn('lib.Total', targets)
        self.assertNotIn('lib.Private', targets)
        self.assertIn('lib.ordinary', targets)
        self.assertNotIn('lib.report', targets)
        self.assertIn('main.pxl:World', projection['types'].values())

    def test_studio_state_edit_round_trips_through_native_parser(self):
        source = """// 星 🛰️
record Payload { samples: [int] }
flow Building() step(value: int) -> int {
 output value
}
effect fn main() {}
"""
        graph = self.project(source)['graphs'][0]
        action = dict(kind='add-state', name='memory', type='Payload',
                      initializer='Payload{samples: [1, 2, 3]}')
        script = """const fs = require('node:fs');
const {planEdit} = require('./vscode/src/native-graph-edits');
const {source, graph, action} = JSON.parse(fs.readFileSync(0, 'utf8'));
let result = source;
for (const edit of planEdit(source, graph, action)) result = result.slice(0, edit.start) + edit.text + result.slice(edit.end);
process.stdout.write(result);"""
        candidate = subprocess.check_output(['node', '-e', script],
            input=json.dumps(dict(source=source, graph=graph, action=action)),
            text=True, cwd=Path(__file__).resolve().parents[1])
        updated = self.project(candidate)['graphs'][0]
        self.assertEqual(len(updated['states']), 1)
        cell = updated['states'][0]
        self.assertEqual(candidate[cell['range']['start']:cell['range']['end']],
                         'state memory: Payload = Payload{samples: [1, 2, 3]}')
        span = updated['outputs'][0]['expression']['range']
        self.assertEqual(candidate[span['start']:span['end']], 'value')

    def test_new_state_preserves_full_native_initializer_range(self):
        source = """// 星 🛰️
record Payload { samples: [int] }
flow Building() step() -> [int] {
 state memory: Payload = Payload{samples: [1, 2, 3]}
 output memory.samples
}
effect fn main() {}
"""
        projection = self.project(source)
        graph = projection['graphs'][0]
        state = graph['states'][0]
        self.assertEqual(source[state['range']['start']:state['range']['end']],
                         'state memory: Payload = Payload{samples: [1, 2, 3]}')
        self.assertEqual(source[state['initializer']['start']:state['initializer']['end']],
                         'Payload{samples: [1, 2, 3]}')

    def test_coordinate_projection_uses_native_types_and_original_ranges(self):
        source = """// 星 🛰️
frame World = meters
record Plain { x: float64, y: float64, z: float64 }
effect fn main() {
 let delta = 2.0
 let p = Point3::[World]{z: -3.0, x: 1.0, y: delta + 1.0}
 let v = Vec3::[World]{x: 0.0, y: 1.0, z: 0.0}
 let ordinary = Plain{x: 1.0, y: 2.0, z: 3.0}
 print(p.z)
}
"""
        projection = self.project(source)
        self.assertEqual(projection['diagnostics'], [])
        coordinates = projection['coordinates']
        self.assertEqual(len(coordinates), 2)
        point = next(item for item in coordinates if item['type'].startswith('Point3'))
        fields = {axis['name']: axis for axis in point['axes']}
        self.assertTrue(fields['z']['literal'])
        self.assertFalse(fields['y']['literal'])
        self.assertEqual(source[fields['z']['range']['start']:fields['z']['range']['end']], '-3.0')
        self.assertEqual(source[fields['y']['range']['start']:fields['y']['range']['end']], 'delta + 1.0')

    def test_coordinate_edit_changes_native_neighborhood_result(self):
        source = """frame World = meters
effect fn main() {
 let origin = Point3::[World]{x: 0.0, y: 0.0, z: 0.0}
 let near = Point3::[World]{x: 0.0, y: 0.0, z: 2.0}
 let empty = Space::[World, string,string]("room")
 let first = empty.insert(origin, "observer")
 let world = first.insert(near, "moving")
 print(len(world.neighbors(world.get(0), 2.0)))
}
"""
        coordinates = self.project(source)['coordinates']
        near = next(item for item in coordinates if 'z: 2.0' in source[item['range']['start']:item['range']['end']])
        script = """const fs = require('node:fs');
const {planCoordinateEdit} = require('./vscode/src/native-coordinate-edits');
const {source, coordinates} = JSON.parse(fs.readFileSync(0, 'utf8'));
let result = source;
for (const edit of planCoordinateEdit(source, coordinates, {z:3.0})) result = result.slice(0, edit.start) + edit.text + result.slice(edit.end);
process.stdout.write(result);"""
        changed = subprocess.check_output(['node', '-e', script],
            input=json.dumps(dict(source=source, coordinates=near)), text=True,
            cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(changed, source.replace('z: 2.0', 'z: 3.0'))
        def execute(text):
            response = self.service.request(dict(version=1, operation='compile',
                                                 files={'main.pxl': text}))
            self.assertEqual(response['status'], 'ok', response)
            result = response['result']
            vm = VM(result['bytecode'])
            origins = []
            while not vm.halted:
                vm.step(snapshot=False)
                for value in vm.stack:
                    if isinstance(value, SpaceValue) and len(value.entities) == 2:
                        origins.append(value.entities[1].position.origin)
            self.assertTrue(origins)
            self.assertTrue(all(origin is not None and origin.node == near['node'] for origin in origins))
            return vm.output
        self.assertEqual(execute(source), [1])
        self.assertEqual(execute(changed), [0])

    def test_incomplete_graph_remains_editable_with_syntax_and_diagnostics(self):
        projection = self.project('''fn Add(value: int) -> int = value + 1
flow InProgress() step(value: int) -> int {
 let add = Add(value)
}
effect fn main() {}
''')
        self.assertTrue(projection['diagnostics'])
        graph = projection['graphs'][0]
        self.assertEqual(graph['name'], 'InProgress')
        self.assertEqual(graph['values'][0]['name'], 'add')
        self.assertEqual(graph['dependencies'][0]['source'], graph['inputs'][0]['node'])
        self.assertEqual(graph['dependencies'][0]['target'], graph['values'][0]['node'])
        self.assertEqual(graph['outputs'], [])

    def test_state_initializers_updates_and_unknown_targets_keep_ast_ranges(self):
        source = '''flow Counter() step(delta: int) -> int {
 state previous: int = 1 + 2
 next previous = delta
 output previous
}
flow InProgress() step() -> int {
 instance unknown = Missing()
 output unknown.step()
}
effect fn main() {}
'''
        projection = self.project(source)
        self.assertTrue(projection['diagnostics'])
        counter, pending = projection['graphs']
        state = counter['states'][0]
        self.assertEqual(source[state['initializer']['start']:state['initializer']['end']], '1 + 2')
        self.assertEqual(counter['updates'][0]['state'], 'previous')
        span = counter['updates'][0]['expression']['range']
        self.assertEqual(source[span['start']:span['end']], 'delta')
        self.assertEqual(pending['children'][0]['target'], 'Missing')
        self.assertEqual(pending['children'][0]['inputs'], [])
        self.assertEqual(pending['children'][0]['result'], '')
