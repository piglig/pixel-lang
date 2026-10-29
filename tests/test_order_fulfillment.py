"""The spatial business example must execute, deliver and replay unchanged."""
import json
from pathlib import Path
import unittest

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

    def request(self, operation, **args):
        response = self.compiler.request(dict(version=1, operation=operation, **args))
        self.assertEqual(response['status'], 'ok', response)
        return response['result']

    def code(self, entry='main.pxl'):
        return self.request('compile', files=self.files, entry=entry)['bytecode']

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
