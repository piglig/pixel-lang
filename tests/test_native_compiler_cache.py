"""Concurrent native test drivers share one compilation and recover from failure."""
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.bytecode_helpers import op, program
from tests.native_compiler import _build_lock, _cached_compile


def code():
    return program([op('LOAD', 0), op('RETURN')])


def concurrent_worker(path, ready, release, output):
    path=Path(path)
    def build():
        with path.with_suffix('.builds').open('a') as stream:
            stream.write('build\n')
        output.put('building')
        if not release.wait(10):
            raise RuntimeError('test did not release builder')
        return code()
    ready.wait(timeout=10)
    output.put(_cached_compile(path,'same-source',build))


def hold_lock(path, acquired):
    with _build_lock(Path(path)):
        acquired.set()
        time.sleep(60)


class NativeCompilerCacheTests(unittest.TestCase):
    def test_four_processes_publish_one_native_artifact(self):
        ctx=mp.get_context('spawn')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'compiler.json'
            ready,release,output=ctx.Barrier(5),ctx.Event(),ctx.Queue()
            workers=[ctx.Process(target=concurrent_worker,args=(str(path),ready,release,output)) for _ in range(4)]
            try:
                for worker in workers:worker.start()
                ready.wait(timeout=10)
                self.assertEqual(output.get(timeout=10),'building')
                # Keep the first build unpublished while all other processes
                # contend for the same cache key.
                self.assertFalse(release.wait(0.2))
                release.set()
                results=[output.get(timeout=10) for _ in workers]
                self.assertEqual(results,[code()]*4)
                for worker in workers:
                    worker.join(10)
                    self.assertEqual(worker.exitcode,0)
                self.assertEqual(path.with_suffix('.builds').read_text(),'build\n')
            finally:
                release.set()
                for worker in workers:
                    if worker.is_alive():worker.terminate()
                    worker.join(5)
                output.close()
                output.join_thread()

    def test_terminated_lock_owner_does_not_leave_a_stale_lock(self):
        ctx=mp.get_context('spawn')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'compiler.json'
            acquired=ctx.Event()
            owner=ctx.Process(target=hold_lock,args=(str(path),acquired))
            owner.start()
            try:
                self.assertTrue(acquired.wait(10))
            finally:
                owner.terminate();owner.join(10)
            self.assertEqual(_cached_compile(path,'key',code),code())

    def test_failed_or_corrupt_artifacts_are_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'compiler.json'
            def fail():raise RuntimeError('compiler failed')
            with self.assertRaisesRegex(RuntimeError,'compiler failed'):
                _cached_compile(path,'key',fail)
            self.assertFalse(path.exists())
            invalid={'version':'invalid'}
            encoded=json.dumps(invalid,sort_keys=True,separators=(',',':')).encode()
            path.write_text(json.dumps(dict(key='key',sha256=hashlib.sha256(encoded).hexdigest(),bytecode=invalid)))
            self.assertEqual(_cached_compile(path,'key',code),code())
            with patch(__name__+'.code',side_effect=AssertionError('cache miss')):
                self.assertIsInstance(_cached_compile(path,'key',code),dict)


class NativeServiceBindingTests(unittest.TestCase):
    def test_changed_bytecode_gets_matching_cached_native_and_repairs_corruption(self):
        from tests.native_compiler import native_service
        from tests.test_native_session import session_program
        from pixellang.native_codegen import build_native
        fixture = session_program()
        fixture['functions']['main'].update(locals=2, instructions=[
            op('CALL', 'initialize'), op('BIND', 0), op('LOAD', 0),
            op('CALL', 'advance'), op('BIND', 1), op('PUSH', 0), op('RETURN')])
        with tempfile.TemporaryDirectory() as directory, \
             patch('tests.native_compiler.NATIVE_COMPILER', Path(directory)/'absent'), \
             patch('tempfile.gettempdir', return_value=directory), \
             patch('pixellang.native_codegen.build_native', wraps=build_native) as build:
            first = native_service(fixture)
            self.addCleanup(first.close)
            self.assertTrue(first.resident)
            self.assertEqual(build.call_count, 1)
            second = native_service(fixture)
            self.addCleanup(second.close)
            self.assertEqual(build.call_count, 1)
            mapping = next(Path(directory).rglob('compiler.map.json'))
            metadata = json.loads(mapping.read_text())
            metadata['executableSha256'] = 'corrupt'
            mapping.write_text(json.dumps(metadata))
            repaired = native_service(fixture)
            self.addCleanup(repaired.close)
            self.assertEqual(build.call_count, 2)
            changed = json.loads(json.dumps(fixture))
            changed['constants'][1]['value'] = 2
            other = native_service(changed)
            self.addCleanup(other.close)
            self.assertEqual(build.call_count, 3)
            self.assertNotEqual(first.compiler_sha256, other.compiler_sha256)


class NativeDriverCacheTests(unittest.TestCase):
    def test_repeated_execution_reuses_build_and_changed_program_is_rebuilt(self):
        from tests.native_compiler import execute_native_driver
        from pixellang.native_codegen import build_native
        from pixellang.bootstrap import normalized
        fixture = code()
        fixture['constants'] = [{'type': 'int', 'value': 42}, {'type': 'int', 'value': 0}]
        fixture['functions']['main']['instructions'] = [
            op('PUSH', 0), op('PRINT'), op('PUSH', 1), op('RETURN')]
        with tempfile.TemporaryDirectory() as directory, \
             patch('tests.native_compiler.NATIVE_COMPILER', Path(directory)/'absent'), \
             patch('tempfile.gettempdir', return_value=directory), \
             patch('pixellang.native_codegen.build_native', wraps=build_native) as build:
            for _ in range(2):
                self.assertEqual(execute_native_driver(fixture, {}), [42])
            self.assertEqual(build.call_count, 1)
            fixture['constants'][0]['value'] = 43
            self.assertEqual(execute_native_driver(fixture, {}), [43])
            self.assertEqual(build.call_count, 2)
            mapping = next(path for path in Path(directory).rglob('compiler.map.json')
                           if json.loads(path.read_text())['bytecodeSha256'] ==
                           hashlib.sha256(normalized(fixture).encode()).hexdigest())
            mapping.write_text('{}')
            self.assertEqual(execute_native_driver(fixture, {}), [43])
            self.assertEqual(build.call_count, 3)


if __name__=='__main__':unittest.main()
