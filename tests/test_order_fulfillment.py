"""The spatial business example must execute, deliver and replay unchanged."""
import json
from pathlib import Path
import unittest
import tempfile
from types import SimpleNamespace
from pixellang.application_session import ApplicationSession

from pixellang.native_events import NativeEventRuntime
from pixellang.vm import VM
from tests.native_compiler import native_service

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'examples/order-fulfillment'
EXPECTED = ['accepted', 'pending', [3, 0], 'Insufficient stock', 'pending', [3, 0],
            'accepted', 'paid', [1, 2], 'Tracking number is required', 'paid', [1, 2],
            'accepted', 'shipped', [1, 0], 'Invalid transition: A-100 is shipped',
            'shipped', [1, 0], 'accepted', 'paid', [1, 2], 0.0]


class OrderFulfillmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = native_service()
        cls.addClassCleanup(cls.compiler.close)
        cls.files = {str(p.relative_to(PROJECT)): p.read_text() for p in PROJECT.rglob('*.pxl')}
        cls.files.update({'std/'+p.name: p.read_text() for p in (ROOT/'pixellang/stdlib').glob('*.pxl')})

    def request(self, operation, **args):
        response = self.compiler.request(dict(version=1, operation=operation, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def code(self, entry='main.pxl'):
        return self.request('compile', files=self.files, entry=entry)['bytecode']

    def session(self, **options):
        def request(operation, **args):
            args.pop('cancelled', None)
            return self.compiler.request(dict(version=1, operation=operation, **args))
        return ApplicationSession(SimpleNamespace(request=request), files=self.files,
                                  entry='application.pxl', **options)

    def test_queries_compose_with_collections_and_survive_png_recovery(self):
        expected = [['order/A-100'], 2, [], 0, 'order/A-100', 'paid']
        self.assertEqual(VM(self.code('queries_demo.pxl')).run(), expected)
        image = self.request('export-png', files=self.files, entry='queries_demo.pxl')['image']
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), expected)

    def test_enumeration_requires_no_arguments_and_returns_readonly_captured_values(self):
        prefix = 'frame World=meters\neffect fn main(){\nlet empty=Space::[World,int,int]("test")\nlet world=empty.insert("a",Point3::[World]{x:0.0,y:0.0,z:0.0},1)\n'
        for statement in ('world.entities(1)', 'world.links(1)', 'var values=world.entities(); values[0].payload=2'):
            response = self.compiler.request(dict(version=1, operation='check', files={'main.pxl':prefix+statement+'\n}'}))
            self.assertEqual(response['status'], 'diagnostics', response)
            self.assertTrue(response['diagnostics'])

    def test_studio_changes_use_selected_event_boundaries_without_mutating_history(self):
        session = self.session()
        self.addCleanup(session.close)
        expected = [2, 0, 3, 0, 2, 0]
        for event, count in zip(json.loads((PROJECT/'events.json').read_text()), expected):
            session.request('application-input', dict(valueText=json.dumps(event['input'])))
            status = session.request('application-step', {})
            self.assertEqual(status['changes']['total'], count)
        self.assertIn('Rejected', status['resultText'])
        self.assertIn('InvalidTransition', status['resultText'])
        session.request('application-seek', dict(index=3))
        before = session.runtime.vm.checkpoint()
        state = session.status()
        self.assertEqual(session.runtime.vm.checkpoint(), before)
        rows = state['changes']['spaces'][0]['rows']
        self.assertEqual([(r['category'], r['kind'], r['key']) for r in rows],
            [('entity','updated','depot'), ('entity','updated','order/A-100'), ('link','added','reservation/A-100')])
        self.assertIn('Pending', rows[1]['beforeText'])
        self.assertIn('Paid', rows[1]['afterText'])
        self.assertEqual(session.runtime.cursor, 3)
        session.request('application-seek', dict(index=5))
        self.assertEqual(session.status()['changes']['spaces'][0]['rows'][0]['fields'], ['position','payload'])
        recording = session.request('application-recording', {})
        restored = session.request('application-restore', recording)
        self.assertEqual(restored['changes']['total'], 2)
        self.assertEqual(session.request('application-seek', dict(index=2))['changes']['total'], 0)

    def test_journal_recovery_reconstructs_changes_and_preserves_live_frontier(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'orders.pxjournal'
            session = self.session(journal_path=path)
            session.runtime.checkpoint_interval = 1
            try:
                for event in json.loads((PROJECT/'events.json').read_text())[:3]:
                    session.request('application-input', dict(valueText=json.dumps(event['input'])))
                    session.request('application-step', {})
            finally:
                session.close()
            reopened = self.session(journal_path=path)
            try:
                before = reopened.runtime.vm.checkpoint()
                self.assertEqual(reopened.status()['changes']['total'], 3)
                self.assertEqual(reopened.request('application-seek', dict(index=2))['changes']['total'], 0)
                self.assertEqual(reopened.runtime.vm.checkpoint(), before)
                self.assertEqual(reopened.runtime.completed, 3)
            finally:
                reopened.close()

    def test_business_invariants_in_pixellang(self):
        self.assertEqual(VM(self.code('tests/fulfillment_test.pxl')).run(), [])

    def test_png_and_recovered_project_preserve_business_behavior(self):
        self.assertEqual(VM(self.code()).run(), EXPECTED)
        image = self.request('export-png', files=self.files)['image']
        self.assertEqual(VM(self.request('compile-png', image=image)['bytecode']).run(), EXPECTED)
        recovered = self.request('recover-png', image=image)
        self.assertEqual(VM(self.request('compile', **recovered)['bytecode']).run(), EXPECTED)

    def test_rejections_do_not_publish_drafts_and_recordings_restore_history(self):
        code = self.code('application.pxl')
        runtime = NativeEventRuntime(VM(code))
        outputs = []
        for event in json.loads((PROJECT / 'events.json').read_text()):
            runtime.enqueue(event['input'])
            outputs.append(runtime.step())
        self.assertEqual([v['outcome']['variant'] for v in outputs],
                         ['Accepted', 'Rejected', 'Accepted', 'Rejected', 'Accepted', 'Rejected'])
        # Equality includes entities, payloads, links, positions and snapshot version.
        self.assertEqual(outputs[0]['scene'], outputs[1]['scene'])
        self.assertEqual(outputs[2]['scene'], outputs[3]['scene'])
        self.assertEqual(outputs[4]['scene'], outputs[5]['scene'])
        self.assertEqual(outputs[2]['scene']['links'][0]['identity'], 'reservation/A-100')
        self.assertEqual(outputs[4]['scene']['links'], [])
        self.assertEqual(len(runtime.records), 6)
        self.assertEqual(runtime.pending, [])
        restored = NativeEventRuntime.restore(VM(code), json.loads(json.dumps(runtime.recording())))
        self.assertEqual(restored.vm.checkpoint(), runtime.vm.checkpoint())
        for index in (3, 1, 6, 2, 5):
            self.assertEqual(restored.seek(index), outputs[index - 1])


if __name__ == '__main__':
    unittest.main()
