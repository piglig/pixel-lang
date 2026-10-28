"""Native spatial/function semantics survive actual PNG delivery and recovery."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pixellang.bootstrap_image import HOST_STAGES
from pixellang.compiler_client import CompilerClient
from pixellang.workstation import Workstation
from pixellang.compiler_service import CompilerService
from pixellang.codec import rgba
from pixellang.model import PixelError
from pixellang.picture import encode_picture
from pixellang.debug_artifact import prepare_debug
from pixellang.vm import VM
from tests.native_compiler import load_native_compiler
from tests.test_selfhost_png import png_data


class NativePixelDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = load_native_compiler(driver=None)

    def setUp(self):
        self.service = CompilerService(self.code)

    def test_native_elevator_example_roundtrip_and_recovered_spatial_edit(self):
        root = Path(__file__).resolve().parents[1] / 'examples/native-elevator'
        files = {path.name: path.read_text() for path in root.glob('*.pxl')}
        expected = [
            [0, 3, 0], False, [0], 'rejected: Cannot open doors during a trip',
            [1, 3, 0], False, [1], [2, 3, 0], False, [2],
            [3, 3, 1], True, [3], [3, 1, 0], False, [3],
            [2, 1, 0], False, [2], [1, 1, 1], True, [1],
            [0, 0, 1], True, [0], 9.0, 1,
        ]
        recovered = self.roundtrip(files, expected)
        projection = self.request('native-graphs', **recovered)['projection']
        self.assertEqual(len(projection['graphs']), 1)
        graph = projection['graphs'][0]
        self.assertEqual((len(graph['values']), len(graph['states']), len(graph['updates'])), (3, 2, 2))
        self.assertTrue(graph['dependencies'])
        # Edit the recovered cabin expression by native source ranges. Its Y
        # coordinate is computed; only the literal Z is changed.
        candidate = None
        for coordinates in projection['coordinates']:
            axes = {axis['name']: axis for axis in coordinates['axes']}
            source = recovered['files'][coordinates['source']]
            x = axes['x']['range']
            if not axes['y']['literal'] and source[x['start']:x['end']] == '0.0':
                candidate = coordinates
                break
        self.assertIsNotNone(candidate)
        z = next(axis['range'] for axis in candidate['axes'] if axis['name'] == 'z')
        source = recovered['files'][candidate['source']]
        edited = {**recovered['files'], candidate['source']: source[:z['start']] + '2.0' + source[z['end']:]}
        code = self.request('compile', files=edited, entry=recovered['entry'])['bytecode']
        changed = [([item[0], item[1], 0] if len(item) == 3 else []) if isinstance(item, list) else item
                   for item in expected]
        changed[-1] = 0
        self.assertEqual(VM(code).run(), changed)

    def test_typed_links_and_weights_survive_png_delivery(self):
        source = '''frame World = meters
record Cost { weight: int, enabled: bool }
effect fn main() {
 let empty = Space::[World,string,Cost]("network")
 let first = empty.insert(Point3::[World]{x:0.0,y:0.0,z:0.0}, "a")
 let nodes = first.insert(Point3::[World]{x:0.0,y:0.0,z:2.0}, "b")
 let connected = nodes.connect(nodes.get(0), nodes.get(1), Cost{weight:5,enabled:true})
 let link: Link[World,string,Cost] = connected.getLink(0)
 let moved = connected.move(connected.get(1), Point3::[World]{x:0.0,y:0.0,z:9.0})
 let updated = moved.setLinkPayload(link, Cost{weight:8,enabled:true})
 var total = 0
 for edge in updated.outgoing(updated.get(0)) {
  let cost = edge.payload
  if cost.enabled { total += cost.weight }
 }
 print(total)
 print(link.payload.weight)
 print(connected.target(link).position.z)
 print(updated.target(link).position.z)
 print(updated.source(link).id)
 print(updated.disconnect(link).linkCount())
}
'''
        self.roundtrip({'main.pxl': source}, [8, 5, 2.0, 9.0, 0, 0])

    def request(self, operation, **args):
        with ExitStack() as stack:
            for target in HOST_STAGES:
                stack.enter_context(patch(target, side_effect=AssertionError('Host frontend used')))
            response = self.service.request(dict(version=1, operation=operation, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def roundtrip(self, files, expected):
        original = self.request('compile', files=files)['bytecode']
        self.assertEqual(VM(original).run(), expected)
        image = self.request('export-png', files=files)['image']
        self.assertTrue(image.startswith(b'\x89PNG'))
        restored = self.request('compile-png', image=image)['bytecode']
        self.assertEqual(VM(restored).run(), expected)
        recovered = self.request('recover-png', image=image)
        rebuilt = self.request('compile', **recovered)['bytecode']
        self.assertEqual(VM(rebuilt).run(), expected)
        self.assertEqual(len(original.get('coordinateFrames', {})), len(restored.get('coordinateFrames', {})))
        self.assertTrue(original.get('effectFunctions'))
        self.assertEqual(len(original.get('effectFunctions', [])), len(restored.get('effectFunctions', [])))
        def shapes(code):
            return sorted((len(graph['values']), len(graph['states']), len(graph['dependencies']), len(graph['updates']), len(graph['children']))
                          for graph in code.get('flows', {}).values())
        self.assertEqual(shapes(original), shapes(restored))
        self.assertEqual(shapes(original), shapes(rebuilt))
        return recovered

    def test_overlapping_pixel_origins_keep_independent_logical_ranges(self):
        files = {'main.pxl': 'fn wrap[T](x:T)->T=x\neffect fn main(){var x=2; x+=wrap(3);print(wrap(x)*2);print(wrap("ok"))}'}
        image = self.request('export-png', files=files)['image']
        code = self.request('compile-png', image=image)['bytecode']
        self.assertEqual(VM(code).run(), [10, 'ok'])
        ranges = {}
        for function in code['functions'].values():
            for instruction in function['instructions']:
                span = instruction.get('span')
                if not span or span['kind'] != 'spatial':
                    continue
                points = tuple(tuple(point) for point in span['points'])
                self.assertTrue(points)
                self.assertEqual(span['min'], [min(p[0] for p in points), min(p[1] for p in points)])
                self.assertEqual(span['max'], [max(p[0] for p in points), max(p[1] for p in points)])
                self.assertIn((span['start']['column'] - 1, span['start']['line'] - 1), points)
                self.assertLess(span['start']['offset'], span['end']['offset'])
                ranges.setdefault((span['source'], points), set()).add(
                    (span['start']['offset'], span['end']['offset']))
        # Identical geometry does not make distinct AST origins interchangeable.
        self.assertTrue(any(len(origins) > 1 for origins in ranges.values()))

    def test_function_coordinate_frames_and_imported_transform_roundtrip(self):
        files = {'lib.pxl': '''export frame World = meters
export frame Local = centimeters
export fn Advance(p: Point3[World], v: Vec3[World]) -> Point3[World] = p + v
''', 'main.pxl': '''import "lib.pxl" as lib
effect fn main() {
 let p = Point3::[lib.Local]{x: 0.0, y: 0.0, z: 200.0}
 let transform = Transform3::[lib.Local, lib.World]{rotation: [[1.0,0.0,0.0],[0.0,1.0,0.0],[0.0,0.0,1.0]], translation: Vec3::[lib.World]{x: 1.0, y: 0.0, z: 0.0}}
 let world = transform.point(p)
 let moved = lib.Advance(world, Vec3::[lib.World]{x: 0.0, y: 0.0, z: 3.0})
 print(world.x)
 print(world.z)
 print(moved.z)
}
'''}
        recovered = self.roundtrip(files, [1.0, 2.0, 5.0])
        text = '\n'.join(recovered['files'].values())
        self.assertIn('fn ', text)
        self.assertIn('frame ', text)
        self.assertIn('centimeters', text)
        self.assertIn('Transform3[', text)

    def test_native_space_queries_and_entity_payload_types_roundtrip(self):
        files = {'main.pxl': '''frame World = meters
record Payload { value: int }
fn Nearby(space: Space[World, Payload,string], origin: Entity[World, Payload]) -> int = len(space.neighbors(origin, 2.0))
effect fn main() {
 var space = Space::[World, Payload,string]("delivery")
 space = space.insert(Point3::[World]{x: 0.0, y: 0.0, z: 0.0}, Payload{value: 7})
 space = space.insert(Point3::[World]{x: 0.0, y: 0.0, z: 1.5}, Payload{value: 9})
 let origin = space.get(0)
 print(Nearby(space, origin))
 print(origin.payload.value)
 print(space.ahead(origin, space.get(1), Vec3::[World]{x: 0.0, y: 0.0, z: 1.0}, 0.0))
}
'''}
        recovered = self.roundtrip(files, [1, 7, True])
        self.assertEqual(sum(text.count('1.5') for text in recovered['files'].values()), 1)
        recovered['files'] = {name: text.replace('1.5', '3.0') for name, text in recovered['files'].items()}
        changed = self.request('compile', **recovered)['bytecode']
        self.assertEqual(VM(changed).run(), [0, 7, True])

    def test_spatial_function_debug_bundle_has_usable_original_source_mapping(self):
        files = {'main.pxl': '''frame World = meters
fn Height(p: Point3[World]) -> float64 = p.z
effect fn main() { print(Height(Point3::[World]{x: 0.0, y: 0.0, z: 3.0})) }
'''}
        bundle = self.request('debug-bundle', files=files)
        prepared = prepare_debug(bundle['bytecode'], bundle['debug'], bundle['document'])
        self.assertIsNotNone(prepared)
        self.assertEqual(VM(bundle['bytecode']).run(), [3.0])
        self.assertIn('main.pxl:World', bundle['debug']['types'].values())
        image = self.request('export-debug-png', files=files)['image']
        recovered = self.request('recover-png', image=image)
        self.assertIn('frame World = meters', recovered['files']['main.pxl'])
        self.assertIn('fn Height(', recovered['files']['main.pxl'])
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), [3.0])

    def test_imported_native_flow_expressions_and_record_projections_roundtrip(self):
        files = {'lib.pxl': '''export record StageResult { value: int, _pixel_80: int }
export fn Add(value: int) -> StageResult = StageResult{value: value + 1, _pixel_80: value + 2}
export flow Stage() step(value: int) -> StageResult {
 let added = Add(value)
 output added
}
''', 'main.pxl': '''import "lib.pxl" as lib
flow Pipeline() step(value: int) -> int {
 instance firstStage = lib.Stage()
 instance secondStage = lib.Stage()
 let first = firstStage.step(value)
 let second = secondStage.step(first._pixel_80)
 output second.value
}
effect fn main() { print(Pipeline().step(40)) }
'''}
        recovered = self.roundtrip(files, [43])
        text = '\n'.join(recovered['files'].values())
        self.assertEqual(text.count('flow '), 2)
        self.assertIn('._pixel_80', text)
        image = self.request('export-debug-png', files=files)['image']
        named = self.request('recover-png', image=image)
        self.assertIn('flow Pipeline(', named['files']['main.pxl'])
        self.assertRegex(named['files']['main.pxl'], r'let first(?:\s*:\s*\w+\.StageResult)?\s*=\s*firstStage\.step\(\w+\)')
        self.assertEqual(VM(self.request('compile', **named)['bytecode']).run(), [43])
        projection = self.request('native-graphs', **named)['projection']
        self.assertEqual(projection['diagnostics'], [])
        pipeline = next(graph for graph in projection['graphs'] if graph['name'] == 'Pipeline')
        self.assertEqual(len(pipeline['values']), 2)
        self.assertEqual([child['name'] for child in pipeline['children']], ['firstStage', 'secondStage'])
        first, second = pipeline['values']
        self.assertTrue(any(edge['source'] == first['node'] and edge['target'] == second['node']
                            for edge in pipeline['dependencies']))

    def test_nested_stateful_flow_handles_roundtrip_and_debug_mapping(self):
        files = {'lib.pxl': '''export fn Add(previous: int, delta: int) -> int = previous + delta
export flow Counter() step(delta: int) -> int {
 state previous: int = 0
 let added = Add(previous, delta)
 next previous = added
 output added
}
''', 'main.pxl': '''import "lib.pxl" as lib
flow Twice() step(delta: int) -> int {
 instance first = lib.Counter()
 instance second = lib.Counter()
 let firstValue = first.step(delta)
 output second.step(firstValue)
}
effect fn main() {
 let a: Flow[fn(int)->int] = Twice()
 let b = Twice()
 print(a.step( 1))
 print(b.step( 10))
 print(a.step( 2))
 print(a.step( 3))
}
'''}
        recovered = self.roundtrip(files, [1, 10, 4, 10])
        self.assertIn('state ', '\n'.join(recovered['files'].values()))
        self.assertIn('next ', '\n'.join(recovered['files'].values()))
        bundle = self.request('debug-bundle', files=files)
        prepared = prepare_debug(bundle['bytecode'], bundle['debug'], bundle['document'])
        self.assertEqual(VM(prepared.bytecode).run(), [1, 10, 4, 10])
        self.assertTrue(any('.initial.' in item['name'] for item in bundle['debug']['functions'].values()))

    def test_native_space_state_initializers_keep_instance_isolation_after_png(self):
        files = {'main.pxl': '''frame World = meters
fn Insert(space: Space[World, int,string], z: float64) -> Space[World, int,string] = space.insert(Point3::[World]{x: 0.0, y: 0.0, z: z}, 7)
fn Size(space: Space[World, int,string]) -> int = space.size()
flow Points() step(z: float64) -> int {
 state space: Space[World, int,string] = Space::[World, int,string]("points")
 let inserted = Insert(space, z)
 let count = Size(inserted)
 next space = inserted
 output count
}
effect fn main() {
 let a = Points()
 let b = Points()
 print(a.step( 1.0))
 print(a.step( 2.0))
 print(b.step( 1.0))
}
'''}
        self.roundtrip(files, [1, 2, 1])

    def test_malformed_native_graph_pixels_are_rejected_before_execution(self):
        files = {'main.pxl': '''fn Identity(value: int) -> int = value
flow Pipeline() step(value: int) -> int {
 let identity = Identity(value)
 output identity
}
effect fn main() { print(Pipeline().step(7)) }
'''}
        original = self.request('pixels', files=files)['document']
        # Keep a valid function header, then place a flow-only member beneath it.
        # Renaming a flow header to fn now fails earlier at its explicit step list.
        wrong_parent = self.request('pixels', files={'main.pxl':
            'fn Identity(value: int) -> int { return value }\neffect fn main() { print(Identity(7)) }'
        })['document']
        member = next(pixel for pixel in wrong_parent['pixels'] if pixel['rgba'] == rgba('keyword', 'return'))
        member['rgba'] = rgba('keyword', 'output')
        wrong_reference = deepcopy(original)
        pixels = wrong_reference['pixels']
        output = next(i + 1 for i in range(len(pixels) - 1)
                      if pixels[i]['rgba'] == rgba('keyword', 'output'))
        self.assertEqual(pixels[output]['rgba'][0], 32)
        pixels[output]['rgba'] = rgba('id', 65535)
        for document, message in ((wrong_parent, 'Flow member requires a flow parent'),
                                  (wrong_reference, 'Unknown name')):
            with self.subTest(message=message):
                # Wrap deliberately corrupted native pixels as a filter-0 PNG
                # fixture without invoking the guarded host PNG encoder.
                image = encode_picture(document, scale=4)
                raw = image.tobytes()
                stride = image.width * 4
                rows = b''.join(b'\0' + raw[offset:offset + stride]
                                for offset in range(0, len(raw), stride))
                fixture = png_data(image.width, image.height, rows)
                with ExitStack() as guards:
                    for target in HOST_STAGES:
                        guards.enter_context(patch(target, side_effect=AssertionError('Host frontend used')))
                    result = self.service.request(dict(version=1, operation='compile-png', image=fixture))
                self.assertEqual(result['status'], 'diagnostics', result)
                self.assertTrue(any(message in item['message'] for item in result['diagnostics']), result)
                self.assertIsNone(result['result'])

    def test_workstation_build_step_record_restore_and_native_flow_scope(self):
        # Exercise the real workstation/client/service boundary with the tested
        # current compiler, without replacing the distributed compiler artifact.
        worker = SimpleNamespace(request=lambda request, cancelled, submitted_at:
                                 self.service.request(request, cancelled=cancelled))
        workstation = Workstation.__new__(Workstation)
        workstation.compiler = CompilerClient(worker)
        workstation.sessions = {}
        workstation.build_cache = None
        source = '''fn Add(previous: int, delta: int) -> int = previous + delta
flow Counter() step(delta: int) -> int {
 state previous: int = 0
 let added = Add(previous, delta)
 next previous = added
 output added
}
effect fn main() {
 let counter = Counter()
 print(counter.step( 1))
 print(counter.step( 2))
}
'''
        built = workstation.request('build', dict(files={'main.pxl': source}, scale=4))
        session = built['session']
        active = None
        status = built
        for _ in range(200):
            if status['state']['halted']:
                break
            status = workstation.request('step', dict(session=session))
            if status['flow_scopes']:
                active = status['cursor']
        self.assertTrue(status['state']['halted'])
        self.assertEqual(status['output'], ['1', '3'])
        self.assertIsNotNone(active)
        historic = workstation.request('seek', dict(session=session, z=active))
        self.assertTrue(historic['flow_scopes'])
        self.assertEqual(historic['location']['source'], 'main.pxl')
        recording = workstation.request('recording', dict(session=session))
        self.assertEqual(recording['backend'], 'selfhost')
        recording = json.loads(json.dumps(recording))
        restored = workstation.request('restore', dict(document=recording))
        self.assertEqual(restored['cursor'], active)
        self.assertTrue(restored['flow_scopes'])
        final = workstation.request('run', dict(session=restored['session']))
        self.assertEqual(final['output'], ['1', '3'])
        self.assertTrue(final['state']['halted'])

    def test_native_space_recording_restores_staged_transaction_and_provenance(self):
        worker = SimpleNamespace(request=lambda request, cancelled, submitted_at:
                                 self.service.request(request, cancelled=cancelled))
        workstation = Workstation.__new__(Workstation)
        workstation.compiler = CompilerClient(worker)
        workstation.sessions = {}
        workstation.build_cache = None
        source = '''frame World = meters
fn Update(world: Space[World, string,int], position: Point3[World]) -> Space[World, string,int] {
 if world.size() == 0 {
  let first = world.insert(Point3::[World]{x: 0.0, y: 0.0, z: 0.0}, "observer")
  let populated = first.insert(position, "moving")
  return populated.connect(populated.get(0), populated.get(1), 5)
 }
 let moved = world.move(world.get(1), position)
 return moved.setLinkPayload(moved.getLink(0), 8)
}
flow Simulation() step(position: Point3[World]) -> Space[World, string,int] {
 state world: Space[World, string,int] = Space::[World, string,int]("room")
 let updated = Update(world, position)
 next world = updated
 output updated
}
effect fn main() {
 let simulation = Simulation()
 let before = simulation.step( Point3::[World]{x: 0.0, y: 0.0, z: 2.0})
 let after = simulation.step( Point3::[World]{x: 0.0, y: 0.0, z: 3.0})
 print(len(before.neighbors(before.get(0), 2.0)))
 print(len(after.neighbors(after.get(0), 2.0)))
 print(before.get(1).position.z)
 print(after.get(1).position.z)
 print(before.getLink(0).payload)
 print(after.getLink(0).payload)
}
'''
        with ExitStack() as guards:
            for target in HOST_STAGES:
                guards.enter_context(patch(target, side_effect=AssertionError('Host frontend used')))
            built = workstation.request('build', dict(files={'main.pxl': source}, scale=4, checkpoint_interval=8))
            session = built['session']
            timeline = workstation.sessions[session]
            selected = None
            for _ in range(500):
                status = workstation.request('step', dict(session=session))
                transaction = timeline.vm.flow_runtime.transaction
                if transaction and transaction.staged and timeline.vm.flow_runtime.instances[0].steps == 1:
                    selected = status['cursor']
                    break
                if status['state']['halted']:
                    break
            self.assertIsNotNone(selected)
            frame = status['flow_scopes'][0]['path'][1]
            old_path = ['flow', frame, 'oldState', 'world']
            next_path = ['flow', frame, 'nextState', 'world', 'value']
            def view(current_session, path, **options):
                return workstation.request('native-space-view', dict(session=current_session, z=selected, path=path, **options))
            original_old, original_next = view(session, old_path), view(session, next_path)
            self.assertEqual([e['position'][2] for e in original_old['entities']], [0.0, 2.0])
            self.assertEqual([e['position'][2] for e in original_next['entities']], [0.0, 3.0])
            self.assertEqual(original_old['identity'], original_next['identity'])
            self.assertEqual(original_old['links'][0]['payload']['value'], '5')
            self.assertEqual(original_next['links'][0]['payload']['value'], '8')
            self.assertEqual([original_old['links'][0][key] for key in ('id', 'source', 'target')],
                             [original_next['links'][0][key] for key in ('id', 'source', 'target')])
            self.assertEqual([e['id'] for e in original_old['entities']], [0, 1])
            self.assertIsNotNone(original_next['entities'][1]['origin'])
            self.assertNotEqual(original_old['entities'][1]['origin'], original_next['entities'][1]['origin'])
            final = workstation.request('run', dict(session=session))
            self.assertEqual(final['output'], ['1', '0', '2.0', '3.0', '5', '8'])
            workstation.request('seek', dict(session=session, z=selected))
            recording = json.loads(json.dumps(workstation.request('recording', dict(session=session))))
            # Delete the original session so inspection cannot reuse its live VM.
            workstation.request('close', dict(session=session))
            restored = workstation.request('restore', dict(document=recording))
            restored_session = restored['session']
            self.assertEqual(restored['cursor'], selected)
            self.assertEqual(view(restored_session, old_path), original_old)
            self.assertEqual(view(restored_session, next_path), original_next)
            self.assertEqual(view(restored_session, old_path, neighbors=dict(origin=0, radius=2.0))['total'], 1)
            self.assertEqual(view(restored_session, next_path, neighbors=dict(origin=0, radius=2.0))['total'], 0)
            continued = workstation.request('run', dict(session=restored_session))
            self.assertTrue(continued['state']['halted'])
            self.assertEqual(continued['output'], final['output'])
            tampered = deepcopy(recording)
            tampered['source_project']['files']['main.pxl'] = source.replace('z: 3.0', 'z: 4.0')
            with self.assertRaisesRegex(PixelError, 'differs from recorded bytecode'):
                workstation.request('restore', dict(document=tampered))

            # Delivery is a native PNG program. Recover it before recording a
            # fresh, partially executed transaction, without the original VM.
            image = self.request('export-debug-png', files={'main.pxl': source})['image']
            recovered = self.request('recover-png', image=image)
            rebuilt = workstation.request('build', dict(**recovered, scale=4))
            delivered_session = rebuilt['session']
            delivered = workstation.sessions[delivered_session]
            for _ in range(500):
                status = workstation.request('step', dict(session=delivered_session))
                transaction = delivered.vm.flow_runtime.transaction
                if transaction and transaction.staged and delivered.vm.flow_runtime.instances[0].steps == 1:
                    break
                if status['state']['halted']:
                    self.fail('Recovered program did not expose its staged space state')
            else:
                self.fail('Recovered program exceeded the test step bound')
            delivered_frame = status['flow_scopes'][0]['path'][1]
            delivered_graph = workstation.request('flow-graph', dict(session=delivered_session, z=status['cursor'], frame=delivered_frame))
            self.assertEqual(len(delivered_graph['states']), 1)
            # Recovery may disambiguate repeated source names; use the native
            # graph's state identity rather than an original spelling.
            state_name = delivered_graph['states'][0]['name']
            delivered_args = dict(z=status['cursor'], path=['flow', delivered_frame, 'nextState', state_name, 'value'])
            delivered_view = workstation.request('native-space-view', dict(session=delivered_session, **delivered_args))
            self.assertEqual(delivered_view['entities'][1]['position'][2], 3.0)
            self.assertIsNotNone(delivered_view['entities'][1]['origin'])
            delivered_recording = json.loads(json.dumps(workstation.request('recording', dict(session=delivered_session))))
            workstation.request('close', dict(session=delivered_session))
            reopened = workstation.request('restore', dict(document=delivered_recording))
            self.assertEqual(workstation.request('native-space-view', dict(session=reopened['session'], **delivered_args)), delivered_view)
            self.assertEqual(workstation.request('run', dict(session=reopened['session']))['output'], final['output'])
