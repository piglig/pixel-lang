"""Long project jobs cannot block editor requests or read-only workspace snapshots."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

from pixellang.workstation import WorkstationLane


def request(method, identity=1):
    return dict(version=1, id=identity, method=method, args={})


class WorkstationLaneTests(unittest.TestCase):
    def exercise(self, slow, fast):
        entered, release = threading.Event(), threading.Event()

        class Service:
            def request(self, method, args, **kwargs):
                if method == slow:
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('Test did not release job')
                return dict(method=method)

        lane = WorkstationLane(Service())
        with ThreadPoolExecutor(max_workers=3) as pool:
            job = pool.submit(lane.request, request(slow))
            try:
                self.assertTrue(entered.wait(1))
                for index, method in enumerate(fast):
                    response = pool.submit(lane.request, request(method, index+2)).result(timeout=1)
                    self.assertEqual(response['status'], 'ok')
                    self.assertEqual(response['result']['method'], method)
                self.assertFalse(job.done())
            finally:
                release.set()
            self.assertEqual(job.result(timeout=1)['status'], 'ok')

    def test_project_work_does_not_block_editor_or_workspace(self):
        self.exercise('project-test', ['workspace', 'check', 'build'])

    def test_workspace_reads_do_not_wait_for_compilation(self):
        self.exercise('build', ['workspace'])

    def test_background_analysis_does_not_block_build_or_debugging(self):
        self.exercise('document-symbols', ['build', 'run', 'step'])

    def test_build_does_not_block_editor_queries(self):
        self.exercise('build', ['check', 'hover', 'document-symbols'])

    def test_real_compiler_workers_are_isolated_and_closed(self):
        from pixellang.workstation import Workstation
        service = Workstation()
        self.addCleanup(service.close)
        self.assertIsNot(service.compiler.worker, service.editor_compiler.worker)
        from unittest.mock import patch
        with patch.object(service.compiler, 'close') as build_close, \
                patch.object(service.editor_compiler, 'close') as editor_close:
            service.close()
            build_close.assert_called_once()
            editor_close.assert_called_once()

    def test_native_build_completes_while_editor_compiler_is_busy(self):
        from pixellang.workstation import Workstation
        from unittest.mock import patch
        service = Workstation()
        self.addCleanup(service.close)
        lane = WorkstationLane(service)
        entered, release = threading.Event(), threading.Event()

        def slow_check(*args, **kwargs):
            entered.set()
            if not release.wait(15):
                raise RuntimeError('Editor was not released')
            return {'metrics': {}, 'compilerSha256': 'test'}

        files = {'main.pxl': 'effect fn main() { print(42) }'}
        with patch.object(service.editor_compiler, 'request', side_effect=slow_check), \
                ThreadPoolExecutor(max_workers=2) as pool:
            check = pool.submit(lane.request, dict(method='check', args={'files': files}))
            try:
                self.assertTrue(entered.wait(1))
                build = pool.submit(lane.request, dict(method='build', args={'files': files})).result(timeout=10)
                self.assertEqual(build['status'], 'ok', build)
                self.assertFalse(check.done())
                result = lane.request(dict(method='run', args={'session': build['result']['session']}))
                self.assertEqual(result['result']['output'], ['42'])
            finally:
                release.set()
            self.assertEqual(check.result(timeout=1)['status'], 'ok')

    def test_session_requests_stay_serialized_and_wait_is_cancellable(self):
        class Service:
            def request(self, *args, **kwargs):
                raise AssertionError('Cancelled request entered session state')
        lane = WorkstationLane(Service())
        lane.lock.acquire()
        try:
            response = lane.request(request('run'), cancelled=lambda: True)
            self.assertEqual(response['status'], 'cancelled')
        finally:
            lane.lock.release()


if __name__ == '__main__':
    unittest.main()
