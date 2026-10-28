"""Run language and real-project regressions using an existing compiler artifact.

Run with the repository Python environment. No compiler is built by this runner.
Whole-compiler checking/lowering belongs to the separate bootstrap gate; all other
checker and IR tests are reused without changing their assertions.
"""

import argparse
from contextlib import ExitStack, nullcontext
import hashlib
import json
from pathlib import Path
import sys
import unittest
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pixellang.bootstrap_image import HOST_STAGES
from pixellang.bootstrap import read_bytecode_stream, compile_with
from pixellang.compiler_service import CompilerService
from pixellang.regression import run
from pixellang.parallel_regression import run_parallel, shards, timing_costs
from pixellang.vm import VM, validate_bytecode
from pixellang.code_image import CodeImage
from tests.test_expression_flow_pixels import ExpressionFlowPixelTests
from tests.test_immutable_compiler_integration import ImmutableCompilerIntegrationTests
from tests.test_native_type_capabilities import NativeTypeCapabilityTests
from tests.test_selfhost_lexer import SelfHostedLexerTests
from tests.test_selfhost_parser import SelfHostedParserTests
from tests.test_selfhost_checker import SelfHostedCheckerTests
from tests.test_selfhost_ir import SelfHostedIRTests
from tests.test_selfhost_recovery import SelfHostedRecoveryTests
from tests.test_compiler_service import CompilerServiceTests
from tests.test_incremental_ir import IncrementalIRTests
from tests.test_semantic_incremental import SemanticIncrementalTests
from tests.test_selfhost_streaming import SelfhostStreamingTests
from tests.test_native_pixel_delivery import NativePixelDeliveryTests
from tests.test_native_spatial_execution import NativeSpatialExecutionTests
from tests.test_native_link_execution import NativeLinkExecutionTests
from tests.test_native_editor import NativeEditorTests
from tests.test_native_graph_service import NativeGraphServiceTests
from tests.test_native_callable_contracts import NativeCallableContractTests
from tests.test_native_equality import NativeEqualityTests
from tests.test_native_constraints import NativeConstraintTests
from tests.test_native_results import NativeResultTests
from tests.test_native_language_boundaries import NativeLanguageBoundaryTests
from tests.test_native_checked_operations import NativeCheckedOperationTests
from tests.test_native_typed_maps import NativeTypedMapTests
from tests.test_native_format import NativeFormatTests
from tests.test_native_time import NativeTimeTests
from tests.test_native_record_update import NativeRecordUpdateTests
from tests.test_native_value_assignment import NativeValueAssignmentTests
from tests.test_native_generic_values import NativeGenericValueTests
from tests.test_native_collection_methods import NativeCollectionMethodTests
from tests.test_native_value_captures import NativeValueCaptureTests
from tests.test_native_inout import NativeInoutTests
from tests.test_native_inout_png import NativeInoutPNGTests
from tests.test_native_function_effects import NativeFunctionEffectTests, NativeFunctionEffectPNGTests
from tests.test_stdlib_contracts import StandardLibraryContractTests
from tests.test_native_applications import NativeApplicationsTests
from tests.test_native_flow_constructors import NativeFlowConstructorTests
from tests.test_native_graph_constructor_edits import NativeGraphConstructorEditTests
from tests.test_native_control_flow import NativeControlFlowTests
from tests.test_native_match_value_execution import NativeMatchValueExecutionTests
from tests.test_native_value_boundaries import ValueBlockBoundaryTests
from tests.test_native_match_value_delivery import NativeMatchValueDeliveryTests
from tests.test_spatialedit import SpatialEditTests
from tests.test_native_enum_boundaries import EnumBoundaryTests
from tests.test_native_option_inference import OptionInferenceTests
from tests.test_inference import InferenceTests
from tests.test_enums import EnumTests
from tests.test_syntax import SyntaxTests
from tests.test_captures import CaptureAnalysisTests
from tests.test_selfhost_pixel_ast import SelfHostedPixelASTTests
from tests.test_native_type_diagnostics import NativeTypeDiagnosticTests
from tests.test_binary_data import BinaryDataTests
from tests.test_png_canvas import PNGCanvasTests, DRIVER as PNG_CANVAS_DRIVER


def digest(data):
    return hashlib.sha256(data).hexdigest()


def artifact_suite(code, *, native_executable=None):
    suite = unittest.TestSuite()
    image = CodeImage(code)

    def candidate_service(*, limits=None):
        return CompilerService(image, limits=limits, native_executable=native_executable)

    # Validate the candidate binding before creating any test instances.
    native_lane = candidate_service().native if native_executable is not None else None

    def setup_client(cls):
        from pixellang.compiler_client import CompilerClient
        service = candidate_service()
        cls.addClassCleanup(service.close)
        class Worker:
            def request(self, request, *, cancelled=lambda: False, submitted_at=None):
                return service.request(request, cancelled=cancelled)
        cls.compiler = CompilerClient(Worker())

    def setup_canvas(cls):
        service = candidate_service()
        try:
            response = service.request(dict(version=1, operation='compile',
                files={'main.pxl': PNG_CANVAS_DRIVER,
                       'png.pxl': (ROOT / 'selfhost/png.pxl').read_text(),
                       'failures.pxl': (ROOT / 'selfhost/failures.pxl').read_text()}, entry='main.pxl'))
        finally:
            service.close()
        if response['status'] != 'ok':
            raise AssertionError(response)
        cls.compiler = response['result']['bytecode']
    omitted = {
        'test_own_compiler_expression_check': 'Covered by the full-source bootstrap gate',
        'test_compiler_source_lowers_to_ir': 'Covered by the full-source bootstrap gate',
    }

    def setup(cls):
        cls.image = image
        cls.bytecode = image
        cls.code = image
        cls.compiler = image
        cls.files = {p.name: p.read_text() for p in sorted((ROOT / "selfhost").glob("*.pxl"))}

    def setup_results(cls):
        cls.service = candidate_service()
        cls.library = (ROOT / 'pixellang/stdlib/result.pxl').read_text()

    def setup_time(cls):
        cls.compiler = candidate_service()
        cls.library = {'std/' + p.name: p.read_text() for p in (ROOT / 'pixellang/stdlib').glob('*.pxl')}

    def setup_applications(cls):
        with patch('tests.test_native_applications.native_service', side_effect=candidate_service):
            NativeApplicationsTests.setUpClass.__func__(cls)

    def candidate_events(request, *, steps, heap_objects=2_000_000, root=None):
        text = json.dumps(request)
        if native_lane is not None:
            with (tempfile.TemporaryDirectory(prefix='pixel-candidate-') if root is None else nullcontext(root)) as directory:
                return native_lane.execute(text, root=directory, steps=steps,
                    output_chars=64_000_000, file_bytes=64_000_000,
                    cancelled=lambda: False, deadline=time.monotonic()+120)
        from pixellang.fileaccess import FileAccess
        return VM(image, input_text=text, file_access=FileAccess(root, root) if root is not None else None, max_steps=steps,
                  max_heap_items=16_000_000, max_heap_objects=heap_objects).run()

    def native_events(self, source, **modules):
        # Exercise the candidate's public stream entry, preserving its events.
        events = candidate_events(dict(source='main.pxl', text='',
            operation='bytecode-stream', files={'main.pxl': source, **modules}),
            steps=50_000_000)
        return [json.dumps(read_bytecode_stream(events)), *events]

    def native_compile(self, source, **modules):
        return json.loads(native_events(self, source, **modules)[0])

    def native_debug(self, source):
        events = candidate_events(dict(source='main.pxl', text='',
            operation='studio-bytecode-stream', files={'main.pxl': source}),
            steps=50_000_000)
        debug = events[-1]
        if isinstance(debug, str):
            debug = json.loads(debug)
        return read_bytecode_stream(events[:-1]), debug

    def native_diagnostics(self, files):
        if isinstance(files, str):
            files = {'main.pxl': files}
        events = candidate_events(dict(source='main.pxl', text='',
            operation='check', files=files), steps=10_000_000, heap_objects=1_000_000)
        return events[0]['diagnostics']

    def integration_events(self, request, directory=None):
        return candidate_events(request, steps=150_000_000, root=directory)

    def pixel_events(self, request):
        return candidate_events(request, steps=100_000_000)

    def native_scan(self, text, source="test.pxl"):
        return candidate_events(dict(source=source, text=text), steps=20_000_000)[0]

    def native_parse(self, source, name="test.pxl"):
        return candidate_events(dict(source=name, text=source, operation='parse'),
                                steps=40_000_000)[0]

    def native_check(self, source):
        files = source if isinstance(source, dict) else {'main.pxl': source}
        return candidate_events(dict(source='main.pxl', text='', operation='check',
                                     files=files), steps=30_000_000)[0]

    def native_lower(self, files, operation="ir"):
        if isinstance(files, str):
            files = {'main.pxl': files}
        event = candidate_events(dict(source='main.pxl', text='', operation=operation,
                                      files=files), steps=100_000_000)[0]
        return json.loads(event)

    for base in (SelfHostedLexerTests, SelfHostedParserTests, SelfHostedCheckerTests, SelfHostedIRTests, SelfHostedRecoveryTests,
                 CompilerServiceTests, IncrementalIRTests, SemanticIncrementalTests, SelfhostStreamingTests,
                 NativeMatchValueExecutionTests, NativeMatchValueDeliveryTests, ValueBlockBoundaryTests,
                 ExpressionFlowPixelTests, ImmutableCompilerIntegrationTests,
                 NativePixelDeliveryTests, NativeSpatialExecutionTests, NativeLinkExecutionTests,
                 NativeEditorTests, NativeGraphServiceTests, NativeCallableContractTests, NativeEqualityTests, NativeConstraintTests, NativeResultTests, NativeLanguageBoundaryTests, NativeCheckedOperationTests, NativeTypedMapTests, NativeFormatTests, NativeFlowConstructorTests, NativeGraphConstructorEditTests, NativeTimeTests, NativeApplicationsTests, NativeControlFlowTests, SpatialEditTests,
                 NativeRecordUpdateTests, NativeValueAssignmentTests, NativeGenericValueTests, NativeCollectionMethodTests, NativeValueCaptureTests, NativeInoutTests, NativeInoutPNGTests, NativeFunctionEffectTests, NativeFunctionEffectPNGTests, StandardLibraryContractTests, EnumBoundaryTests, OptionInferenceTests, InferenceTests, EnumTests, SyntaxTests, CaptureAnalysisTests, SelfHostedPixelASTTests, NativeTypeDiagnosticTests, BinaryDataTests, PNGCanvasTests):
        methods = {
            '__module__': __name__, 'setUpClass': classmethod(setup),
        }
        if native_lane is not None:
            entry = {SelfHostedLexerTests: ('scan', native_scan),
                     SelfHostedParserTests: ('parse', native_parse),
                     SelfHostedCheckerTests: ('check', native_check),
                     SelfHostedIRTests: ('lower', native_lower)}.get(base)
            if entry is not None:
                methods[entry[0]] = entry[1]
        if base is ImmutableCompilerIntegrationTests:
            methods['events'] = integration_events
        if base is SelfHostedPixelASTTests:
            methods['events'] = pixel_events
        if native_lane is not None and base in (IncrementalIRTests, SemanticIncrementalTests, NativePixelDeliveryTests,
                ExpressionFlowPixelTests, SelfHostedRecoveryTests, SelfhostStreamingTests):
            def setup_native_service(self, original=base):
                # Patch only this test module's imported constructor, honoring
                # its bytecode argument and validating its candidate binding.
                def create(code, **kwargs):
                    service = CompilerService(code, **{**kwargs, 'native_executable':native_executable})
                    self.addCleanup(service.close)
                    return service
                factory = 'native_service' if original is SemanticIncrementalTests else 'CompilerService'
                adapter = patch(original.__module__ + '.' + factory, side_effect=create)
                adapter.start()
                self.addCleanup(adapter.stop)
                original.setUp(self)
            methods['setUp'] = setup_native_service
        if base is CompilerServiceTests:
            # This suite must exercise the supplied candidate even when the
            # ordinary integration suite selects the installed native bundle.
            def make_service(self, limits=None):
                service = candidate_service(limits=limits)
                self.addCleanup(service.close)
                return service
            methods['make_service'] = make_service
        if base is NativeTypeDiagnosticTests:
            methods['diagnostics'] = native_diagnostics
        if base is NativeSpatialExecutionTests:
            methods['compile_events'] = native_events
            methods['debug_result'] = native_debug
        if base in (NativeLinkExecutionTests, NativeCheckedOperationTests):
            methods['compile'] = native_compile
        if base in (NativeLanguageBoundaryTests, ValueBlockBoundaryTests, EnumBoundaryTests, OptionInferenceTests, NativeEditorTests, NativeRecordUpdateTests, NativeValueAssignmentTests, NativeGenericValueTests, NativeCollectionMethodTests, NativeValueCaptureTests, NativeInoutTests, NativeInoutPNGTests, NativeFunctionEffectTests, NativeFunctionEffectPNGTests):
            methods['setUpClass'] = classmethod(lambda cls: setattr(cls, 'compiler', candidate_service()))
        if base in (NativeCallableContractTests, NativeEqualityTests, NativeConstraintTests, NativeTypedMapTests, NativeFormatTests, NativeFlowConstructorTests, NativeGraphConstructorEditTests, NativeMatchValueExecutionTests, NativeMatchValueDeliveryTests):
            methods['setUpClass'] = classmethod(lambda cls: setattr(cls, 'service', candidate_service()))
        if base in (NativeTimeTests, StandardLibraryContractTests):
            methods['setUpClass'] = classmethod(setup_time)
        if base is NativeApplicationsTests:
            methods['setUpClass'] = classmethod(setup_applications)
        if base is NativeResultTests:
            methods['setUpClass'] = classmethod(setup_results)
        if base in (NativeControlFlowTests, SpatialEditTests, InferenceTests, EnumTests, SyntaxTests, BinaryDataTests):
            methods['setUpClass'] = classmethod(setup_client)
        if base is PNGCanvasTests:
            methods['setUpClass'] = classmethod(setup_canvas)
        initialize = methods['setUpClass'].__func__
        def setup_owned(cls, initialize=initialize):
            try:
                initialize(cls)
            finally:
                for name in ('compiler', 'service'):
                    service = cls.__dict__.get(name)
                    if isinstance(service, CompilerService):
                        cls.addClassCleanup(service.close)
        methods['setUpClass'] = classmethod(setup_owned)
        generated = type('Generated' + base.__name__, (base,), methods)
        for name in unittest.defaultTestLoader.getTestCaseNames(generated):
            if name not in omitted:
                suite.addTest(generated(name))
    # These cases need only diagnostics, so exercise the candidate's public
    # checker directly. The capability-enumeration case uses its separate driver.
    unit_cases = type('GeneratedUnitValueTypeTests', (NativeTypeCapabilityTests,), {
        '__module__': __name__, 'setUpClass': classmethod(setup),
        'diagnostics': native_diagnostics,
    })
    for name in ('test_unit_is_rejected_in_value_type_arguments_before_codegen',
                 'test_unit_result_callbacks_remain_values',
                 'test_unit_fields_parameters_and_explicit_specializations_are_rejected'):
        suite.addTest(unit_cases(name))
    return suite, omitted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--native-compiler', type=Path,
                        help='Matching native candidate for compiler-service tests; no installed-artifact substitution')
    parser.add_argument('--sources', type=Path, required=True,
                        help='sources.json saved by the compiler bootstrap run')
    parser.add_argument('--output', type=Path, required=True,
                        help='New evidence directory, never overwritten')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--timings', type=Path, help='Previous test evidence used only to balance shards')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--test', action='append', dest='tests', help=argparse.SUPPRESS)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.workers <= 16 or args.timeout <= 0:
        parser.error('Workers must be 1..16 and timeout must be positive')
    artifact = args.compiler.read_bytes()
    source_bytes = args.sources.read_bytes()
    runner_bytes = Path(__file__).read_bytes()
    code = json.loads(artifact)
    validate_bytecode(code)
    sources = json.loads(source_bytes)
    current = {p.name: p.read_text() for p in sorted((ROOT / 'selfhost').glob('*.pxl'))}
    native_files = {}
    if args.native_compiler:
        executable = args.native_compiler.resolve(strict=True)
        native_files = {path: path.read_bytes() for path in (executable, executable.with_name(executable.name + ".map.json"))}
    suite, omitted = artifact_suite(code, native_executable=args.native_compiler)
    if args.tests:
        selected = [test for test in suite if test.id() in args.tests]
        if len(selected) != len(args.tests):
            parser.error('Unknown or duplicate generated-compiler test ID')
        suite = unittest.TestSuite(selected)
    # This gate also exercises the real Studio source-edit implementation.
    # Reject incomplete isolated workspaces before starting expensive VM tests.
    studio_dependencies = {
        '.test_constructor_edits_and_rejections': ('vscode/src/native-graph-edits.js',
                                                   'vscode/test/native-constructor-service.js'),
        '.test_original_ast_edit_recompile_changes_execution': ('vscode/src/native-graph-edits.js',),
        '.test_supply_missing_creation_list': ('vscode/src/native-graph-edits.js',
                                              'vscode/test/native-constructor-service.js'),
    }
    for test in suite:
        for suffix, paths in studio_dependencies.items():
            if test.id().endswith(suffix):
                for path in paths:
                    if not (ROOT / path).is_file():
                        parser.error(f'Generated-compiler Studio regression requires {ROOT / path}')
    selection = dict(
        compiler=str(args.compiler.resolve()), compilerSha256=digest(artifact),
        sources=str(args.sources.resolve()), sourcesSha256=digest(source_bytes),
        runnerSha256=digest(runner_bytes), sourceSnapshotMatchesCurrent=sources == current,
        nativeCompilerFiles={str(path): digest(data) for path, data in native_files.items()},
        omitted=omitted, tests=[test.id() for test in suite],
    )
    with ExitStack() as stack:
        # Block the remaining host parsers and image codecs.
        for target in HOST_STAGES:
            stack.enter_context(patch(target, side_effect=AssertionError(
                'Generated-artifact regression cannot use the host compiler')))
        if args.child:
            summary = run(suite, args.output, ROOT, selection=selection)
        else:
            def command(group, output):
                return [sys.executable, str(Path(__file__).resolve()), '--child', '--compiler',
                        str(args.compiler.resolve()), '--sources', str(args.sources.resolve()),
                        '--output', str(output),
                        *(['--native-compiler', str(args.native_compiler.resolve())] if args.native_compiler else []),
                        *[arg for name in group for arg in ('--test', name)]]
            costs = timing_costs(args.timings) if args.timings else None
            selection['timingCosts'] = costs
            summary = run_parallel(shards(selection['tests'], args.workers, costs), args.output,
                                   ROOT, command, timeout=args.timeout, selection=selection)
    summary['nativeCompilerUnchanged'] = all(path.read_bytes() == data for path, data in native_files.items())
    summary['artifactUnchanged'] = args.compiler.read_bytes() == artifact
    summary['snapshotUnchanged'] = args.sources.read_bytes() == source_bytes
    summary['runnerUnchanged'] = Path(__file__).read_bytes() == runner_bytes
    summary['sourceSnapshotMatchesCurrent'] = sources == current
    summary['verified'] = bool(summary['verified'] and all(summary[key] for key in (
        'artifactUnchanged', 'nativeCompilerUnchanged', 'snapshotUnchanged', 'runnerUnchanged',
        'sourceSnapshotMatchesCurrent')) and not summary['skipped']
        and not summary['expectedFailures'])
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    raise SystemExit(0 if summary['verified'] else 1)


if __name__ == '__main__':
    main()
