"""Resolve and intern types without a second aliased Resolver record."""
import json
import tempfile
import time
from pathlib import Path

from pixellang.native_codegen import build_native
from pixellang.native_execution import NativeExecution

from tests.native_compiler import compile_native_sources
from tests import test_selfhost_types as type_tests

ROOT = Path(__file__).resolve().parents[1]
DRIVER = '''import "model.pxl" as model
import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
record Request { source:string, text:string, operation:string, files:map[string,string] }
record Output { signatures:[model.FunctionSignature], symbols:[model.Symbol], types:[model.TypeInfo], annotations:[model.NodeType], definitions:[model.NodeType], diagnostics:[model.Diagnostic] }
effect fn main() {
 match jsonDecode::[Request](input()) {
  .Ok(request) => { Dispatch(request) },
  .Err(error) => { print(error.message) }
 }
}
effect fn Dispatch(request:Request) {
 let resolved=types.Analyze(bindings.Bind(project.Build(request.files,request.source)))
 print(Output{signatures:resolved.signatures,symbols:resolved.bound.symbols,
              types:resolved.types,annotations:resolved.annotations,
              definitions:resolved.definitions,diagnostics:resolved.diagnostics})
}'''


class NativeTypeOwnerValueTests(type_tests.SelfHostedTypeTests):
    @classmethod
    def setUpClass(cls):
        cls.files = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
        files = {name: cls.files[name] for name in
                 ('model.pxl', 'lexer.pxl', 'parser.pxl', 'project.pxl', 'bindings.pxl', 'types.pxl')}
        cls.bytecode = compile_native_sources({**files, 'main.pxl': DRIVER})

        cls.native_directory = tempfile.TemporaryDirectory(prefix='pixel-types-test-')
        cls.addClassCleanup(cls.native_directory.cleanup)
        cls.native = NativeExecution(build_native(cls.bytecode,
            Path(cls.native_directory.name) / 'types', optimization=1, max_depth=1024))

    def analyze(self, files, *, max_steps=160_000_000, max_output_chars=1_000_000):
        if isinstance(files, str):
            files = {'main.pxl': files}
        events = self.native.execute(json.dumps(dict(source='main.pxl', text='',
            operation='types', files=files)), root=Path(self.native_directory.name),
            steps=max_steps, output_chars=max_output_chars, file_bytes=64_000_000,
            cancelled=lambda: False, deadline=time.monotonic()+120)
        return events[0]

    def test_native_matches_reference_for_valid_and_invalid_types(self):
        for source in ('record Pair {x:int,y:string}\nfn main(){}',
                       'record Broken {x:Missing}\nfn main(){}'):
            with self.subTest(source=source):
                self.assertEqual(self.analyze(source),
                                 type_tests.SelfHostedTypeTests.analyze(self, source))
