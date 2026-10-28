"""Real native process reuse, bounded requests and fault isolation."""
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from pixellang.model import PixelError
from pixellang.native_codegen import build_native, emit_c, entry_session
from pixellang.native_session import NativeSessionExecution
from pixellang.compiler_service import CompilerService
from tests.bytecode_helpers import op, program


def session_program(loop=False):
    code = program([])
    code['effectFunctions'] = ['advance']
    code['constants'] = [dict(type='int', value=0), dict(type='int', value=1)]
    code['functions'] = {
        'main': dict(params=[], locals=0, result='unit', instructions=[op('PUSH', 0), op('RETURN')]),
        'initialize': dict(params=[], locals=0, result='int', instructions=[op('PUSH', 0), op('RETURN')]),
        'advance': dict(params=['int'], locals=2, result='int', instructions=(
            [op('JUMP', 0)] if loop else [op('BUILTIN', 'input'), op('PRINT'), op('LOAD', 0),
                op('PUSH', 1), op('ADD'), op('STORE', 1), op('LOAD', 1), op('PRINT'), op('LOAD', 1), op('RETURN')]))}
    return code


@unittest.skipUnless(shutil.which('clang'), 'C compiler required')
class NativeSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        options = dict(session={'initialize': 'initialize', 'advance': 'advance'}, optimization=0)
        cls.executable = build_native(session_program(), cls.root/'session', **options)
        cls.loop = build_native(session_program(True), cls.root/'loop', **options)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def runner(self, executable=None):
        runner = NativeSessionExecution(executable or self.executable)
        self.addCleanup(runner.close)
        return runner

    def request(self, runner, text='request', **options):
        args = dict(root=self.root, steps=1000, output_chars=1000, file_bytes=1000,
                    deadline=time.monotonic()+5, cancelled=lambda: False)
        args.update(options)
        return runner.execute(text, **args)

    def test_successive_requests_share_state_and_have_exact_boundaries(self):
        runner = self.runner()
        self.assertEqual(self.request(runner, 'first\nmessage'), ['first\nmessage', 1])
        process = runner.process
        for count in range(2, 20):
            self.assertEqual(self.request(runner, '界\x1e'), ['界\x1e', count])
            self.assertIs(runner.process, process)
            self.assertEqual(runner.steps, 10)
        runner.close()
        self.assertIsNotNone(process.poll())
        self.assertEqual(self.request(runner), ['request', 1])

    def test_budgets_reset_per_request_and_faults_reset_state(self):
        runner = self.runner()
        self.assertEqual(self.request(runner, steps=12), ['request', 1])
        self.assertEqual(self.request(runner, steps=10), ['request', 2])
        process = runner.process
        with self.assertRaises(PixelError) as error:
            self.request(runner, steps=9)
        self.assertEqual(error.exception.code, 'service.steps')
        self.assertIsNone(runner.process)
        self.assertIsNotNone(process.poll())
        self.assertEqual(self.request(runner), ['request', 1])
        with self.assertRaises(PixelError):
            self.request(runner, output_chars=1)
        self.assertEqual(self.request(runner), ['request', 1])

    def test_running_cancellation_and_timeout_reap_exact_process(self):
        for reason in ('cancelled', 'timeout'):
            runner = self.runner(self.loop)
            start = time.monotonic()
            children = []
            def cancelled():
                if runner.process is not None and not children:
                    children.append(runner.process)
                return reason == 'cancelled' and time.monotonic()-start > .05
            with self.assertRaises(PixelError) as error:
                self.request(runner, steps=3_000_000_000, cancelled=cancelled,
                             deadline=start+(.05 if reason=='timeout' else 5))
            self.assertEqual(error.exception.code, 'service.'+reason)
            self.assertIsNone(runner.process)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())

    def test_request_validation_and_closed_peer_cannot_reuse_old_state(self):
        runner = self.runner()
        self.request(runner)
        old = runner.process
        with self.assertRaises(ValueError):
            self.request(runner, steps=True)
        self.assertIsNotNone(old.poll())
        self.assertEqual(self.request(runner), ['request', 1])
        runner.process.kill()
        runner.process.wait()
        self.assertEqual(self.request(runner), ['request', 1])

    def test_session_contract_requires_owned_matching_state(self):
        for session in ({}, {'initialize':'missing','advance':'advance'},
                        {'initialize':'main','advance':'advance'},
                        {'initialize':'initialize','advance':'main'}):
            with self.subTest(session=session), self.assertRaises(ValueError):
                emit_c(session_program(), session=session)

    def test_service_selects_declared_session_and_closes_its_process(self):
        service = CompilerService(session_program(), native_executable=self.executable)
        self.addCleanup(service.close)
        self.assertIsInstance(service.native, NativeSessionExecution)
        self.assertEqual(self.request(service.native), ['request', 1])
        process = service.native.process
        service.close()
        self.assertIsNotNone(process.poll())

    def test_invalid_compiler_statistics_discard_live_session(self):
        service = CompilerService(session_program(), native_executable=self.executable)
        self.addCleanup(service.close)
        execute = service.native.execute
        valid = dict(kind='compiler-session', parsed=0, parseHits=0,
                     bindingHits=0, boundDeclarations=0, typeHits=0, typedDeclarations=0,
                     checkedFunctions=0, semanticHits=0, revision=1, retainedNodes=1, irHits=0, irRelocated=0, irNodes=0)
        for tail in ([], [None], [{**valid, 'revision': True}],
                     [{**valid, 'retainedNodes': -1}], [{**valid, 'parsed': '0'}],
                     [{**valid, 'typeHits': True}], [{**valid, 'typedDeclarations': -1}],
                     [{**valid, 'bindingHits': '0'}], [{**valid, 'boundDeclarations': None}],
                     [{k:v for k,v in valid.items() if k != 'typeHits'}]):
            with self.subTest(tail=tail):
                children = []
                def malformed(*args, **kwargs):
                    execute(*args, **kwargs)
                    children.append(service.native.process)
                    return list(tail)
                service.session_revision = 1
                service.session_nodes = 1
                with patch.object(service.native, 'execute', side_effect=malformed):
                    with self.assertRaises(PixelError) as error:
                        service._execute({}, self.root, lambda: False, time.monotonic()+5)
                self.assertEqual(error.exception.code, 'service.invalid-response')
                self.assertIsNone(service.native.process)
                self.assertIsNotNone(children[0].poll())
                self.assertEqual((service.session_revision, service.session_nodes), (0, 0))
                self.assertEqual(self.request(service.native), ['request', 1])


class SessionEntryTests(unittest.TestCase):
    def entry(self):
        code = session_program()
        code['functions']['main'].update(locals=2, instructions=[
            op('CALL', 'initialize'), op('BIND', 0), op('LOAD', 0),
            op('CALL', 'advance'), op('BIND', 1), op('PUSH', 0), op('RETURN')])
        return code

    def wrapper(self, code, name, target):
        code['functions'][name] = dict(params=[], locals=0, result='unit', instructions=[
            op('CALL', target), op('POP'), op('PUSH', 0), op('RETURN')])
        code['entry'] = [name]

    def test_direct_and_recovered_forwarding_entries_bind_identical_contract(self):
        code = self.entry()
        expected = dict(initialize='initialize', advance='advance')
        self.assertEqual(entry_session(code), expected)
        self.wrapper(code, 'recovered', 'main')
        self.assertEqual(entry_session(code), expected)
        self.wrapper(code, 'outer', 'recovered')
        self.assertEqual(entry_session(code), expected)

    def test_extra_work_and_wrong_state_flow_cannot_be_bypassed(self):
        for mode in ('print', 'wrong-slot', 'overwrite-state', 'captures', 'parameters', 'result'):
            with self.subTest(mode=mode):
                code = self.entry()
                main = code['functions']['main']
                if mode == 'print': main['instructions'].insert(0, op('PRINT'))
                elif mode == 'wrong-slot': main['instructions'][2]['arg'] = 1
                elif mode == 'overwrite-state': main['instructions'][4]['arg'] = 0
                elif mode == 'captures': main['captures'] = ['int']
                elif mode == 'parameters': main['params'] = ['int']
                else: main['result'] = 'int'
                with self.assertRaises(ValueError): entry_session(code)

    def test_invalid_or_cyclic_forwarders_are_rejected(self):
        for mode in ('cycle', 'missing', 'effect', 'locals', 'epilogue'):
            with self.subTest(mode=mode):
                code = self.entry()
                self.wrapper(code, 'wrapper', 'main')
                wrapper = code['functions']['wrapper']
                if mode == 'cycle': wrapper['instructions'][0]['arg'] = 'wrapper'
                elif mode == 'missing': wrapper['instructions'][0]['arg'] = 'missing'
                elif mode == 'effect': wrapper['instructions'].insert(0, op('BUILTIN', 'input'))
                elif mode == 'locals': wrapper['locals'] = 1
                else: wrapper['instructions'][2]['arg'] = 1
                with self.assertRaises(ValueError): entry_session(code)
