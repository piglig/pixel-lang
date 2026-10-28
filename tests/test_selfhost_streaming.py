import tempfile
import json
import unittest
from pathlib import Path

from pixellang.compiler_client import CompilerClient
from pixellang.fileaccess import FileAccess
from pixellang.compiler_service import CompilerService
from pixellang.vm import VM


class ArtifactWorker:
    def __init__(self, code):
        self.service = CompilerService(code)

    def request(self, request, *, cancelled=lambda: False, submitted_at=None):
        return self.service.request(request, cancelled=cancelled)


class SelfhostStreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        artifact = Path(__file__).resolve().parents[1] / 'pixellang/artifacts/compiler.json'
        cls.code = json.loads(artifact.read_text())

    def test_source_png_and_recovered_project_share_streaming_and_argument_semantics(self):
        files = {'main.pxl': '''import "std/io.pxl" as io
import "std/cli.pxl" as cli
import "std/path.pxl" as path
effect fn Run() -> Result[bool, Error] {
 let parsed = cli.Parse(cli.Args(), [], ["name"])?
 print(parsed.options["name"])
 let count = io.ForEachLine(path.Join("", "data.txt")?, effect fn(number, line) { print(number); print(line) })?
 print(io.List("")?)
 return .Ok(true)
}
effect fn main(){match Run(){.Ok(done)=>{},.Err(error)=>{assert(false)}}}
'''}
        with tempfile.TemporaryDirectory() as directory, CompilerClient(worker=ArtifactWorker(self.code)) as compiler:
            Path(directory, 'data.txt').write_text('猫\n🌍')
            source = compiler.request('compile', files=files)['result']['bytecode']
            image = compiler.request('export-png', files=files, scale=4)['result']['image']
            decoded = compiler.request('compile-png', image=image)['result']['bytecode']
            recovered = compiler.request('recover-png', image=image)['result']
            rebuilt = compiler.request('compile', files=recovered['files'], entry=recovered['entry'])['result']['bytecode']
            expected = ['hello', 1, '猫', 2, '🌍', [{'name': 'data.txt', 'directory': False}]]
            for bytecode in (source, decoded, rebuilt):
                self.assertEqual(VM(bytecode, arguments=['--name=hello'],
                                    file_access=FileAccess(directory)).run(), expected)
