from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pixellang.cli import main
from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.workspace import Workspace


class SelfHostedCliTests(unittest.TestCase):
    def test_pixel_document_pack_recovery_and_normalization_are_native(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.value())}',
               'lib.pxl':'export fn value()->int { let values=map[int,int]{1:42}; return values[1] }'}
        with TemporaryDirectory() as directory:
            root=Path(directory)
            with CompilerClient() as client:
                document=client.request('debug-pixels',files=files)['result']['document']
            source=root/'program.pixel';source.write_text(json.dumps(document))
            image=root/'program.png';normalized=root/'normalized.pixel'
            from contextlib import ExitStack
            with ExitStack() as stack:
                for target in ('pixellang.picture.save_picture', 'pixellang.codec.save'):
                    stack.enter_context(patch(target,side_effect=AssertionError('Host document path')))
                for command in ('pack','render'):
                    self.assertEqual(main(command,[str(source),'-o',str(image)]),0)
                    output=io.StringIO()
                    with redirect_stdout(output):self.assertEqual(main('run',[str(image),'--json']),0)
                    self.assertEqual(json.loads(output.getvalue())['output'],[42])
                for origin in (source,image):
                    restored=root/(origin.suffix[1:]+'-restored')
                    output=io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(main('unpack',[str(origin),'-o',str(restored),'--json']),0)
                    entry=json.loads(output.getvalue())['entry']
                    self.assertEqual(len(list(restored.glob('*.pxl'))),2)
                    output=io.StringIO()
                    with redirect_stdout(output):self.assertEqual(main('run',[str(restored/entry),'--json']),0)
                    self.assertEqual(json.loads(output.getvalue())['output'],[42])
                self.assertEqual(main('format',[str(source),'-o',str(normalized)]),0)
                first=json.loads(normalized.read_text())
                self.assertEqual(main('format',[str(normalized),'-o',str(normalized)]),0)
                self.assertEqual(json.loads(normalized.read_text()),first)
                output=io.StringIO()
                with redirect_stdout(output):self.assertEqual(main('run',[str(normalized),'--json']),0)
                self.assertEqual(json.loads(output.getvalue())['output'],[42])
                single=root/'single.pxl';single.write_text('preserve')
                self.assertEqual(main('format',[str(image),'-o',str(image)]),0)
                output=io.StringIO()
                with redirect_stdout(output):self.assertEqual(main('run',[str(image),'--json']),0)
                self.assertEqual(json.loads(output.getvalue())['output'],[42])
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(main('format',[str(source),'-o',str(single)]),1)
                self.assertEqual(single.read_text(),'preserve')

    def test_pixel_document_compilation_execution_and_inspection_are_native(self):
        files={'main.pxl':'import "lib.pxl" as lib\neffect fn main(){print(lib.value())}',
               'lib.pxl':'export fn value()->int { let values=map[int,int]{1:42}; return values[1] }'}
        with TemporaryDirectory() as directory:
            root=Path(directory)
            with CompilerClient() as client:
                document=client.request('pixels',files=files)['result']['document']
            source=root/'program.pixel';source.write_text(json.dumps(document))
            bytecode=root/'program.pxb'
            self.assertEqual(main('compile',[str(source),'-o',str(bytecode)]),0)
            for command in ('run','inspect','debug'):
                output=io.StringIO()
                with redirect_stdout(output):
                    status=main(command,[str(source),'--json'])
                self.assertEqual(status,0,output.getvalue())
                result=json.loads(output.getvalue())
                if command=='run':self.assertEqual(result['output'],[42])
                elif command=='inspect':
                    from pixellang.vm import VM
                    self.assertEqual(VM(result['bytecode']).run(),[42])
                else:self.assertTrue(result['trace'])
            before=bytecode.read_bytes()
            source.write_text('{}')
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main('compile',[str(source),'-o',str(bytecode),'--json']),1)
            self.assertEqual(bytecode.read_bytes(),before)

    def test_source_and_source_free_png_run_through_service(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'pixel.toml').write_text('format=1\n[project]\nname="cli"\nentry="main.pxl"\n')
            (root/'main.pxl').write_text('import "lib.pxl" as lib\neffect fn main(){print(lib.value())}')
            (root/'lib.pxl').write_text('export fn value()->int=42')
            output=io.StringIO()
            with redirect_stdout(output):status=main('run',[str(root/'main.pxl'),'--json'])
            self.assertEqual(status,0,output.getvalue())
            self.assertEqual(json.loads(output.getvalue())['output'],[42])
            result=Workspace(root).build()
            (root/'main.pxl').unlink();(root/'lib.pxl').unlink()
            recovered=root/'recovered'
            output=io.StringIO()
            with redirect_stdout(output):
                status=main('unpack',[result['image'],'-o',str(recovered),'--json'])
            self.assertEqual(status,0,output.getvalue())
            restored=json.loads(output.getvalue())
            output=io.StringIO()
            with redirect_stdout(output):status=main('run',[str(recovered/restored['entry']),'--json'])
            self.assertEqual(status,0,output.getvalue())
            self.assertEqual(json.loads(output.getvalue())['output'],[42])
            output=io.StringIO()
            with redirect_stdout(output):status=main('run',[result['image'],'--json'])
            self.assertEqual(status,0,output.getvalue())
            self.assertEqual(json.loads(output.getvalue())['output'],[42])

    def test_inspection_uses_native_stages_for_typed_maps_and_functions(self):
        source = """frame World = meters
fn Copy(value: map[int, string]) -> map[int, string] = value
effect fn main() {
 let table = map[int, string]{1: "one"}
 print(Copy(table)[1])
}
"""
        with TemporaryDirectory() as directory:
            entry=Path(directory)/'main.pxl';entry.write_text(source)
            with patch('pixellang.text.parse_text', side_effect=AssertionError('Host text parser')):
                results={}
                for stage in ('all', 'source', 'debug', 'graph', 'ir', 'bytecode'):
                    output=io.StringIO()
                    with redirect_stdout(output):
                        status=main('inspect',[str(entry),'--stage',stage,'--json'])
                    self.assertEqual(status,0,output.getvalue())
                    results[stage]=json.loads(output.getvalue())
                self.assertEqual(set(results['all']),{'bytecode','source','debug','ir'})
                self.assertTrue(results['source']['pixels'])
                self.assertTrue(results['debug']['functions'])
                self.assertTrue(results['graph'])
                self.assertTrue(results['ir'])
                self.assertEqual(results['bytecode']['magic'],'PIXELVM')
                from pixellang.vm import VM
                self.assertEqual(VM(results['bytecode']).run(),['one'])
                self.assertEqual(VM(results['all']['bytecode']).run(),['one'])

    def test_png_and_bytecode_inspection_and_invalid_bytecode(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            with CompilerClient() as client:
                image=client.request('export-png',files={'main.pxl':'effect fn main(){print(42)}'})['result']['image']
            path=root/'program.png';path.write_bytes(image)
            output=root/'inspect.json'
            self.assertEqual(main('inspect',[str(path),'--stage','bytecode','-o',str(output)]),0)
            code=json.loads(output.read_text())
            bytecode=root/'program.pxb';bytecode.write_text(json.dumps(code))
            self.assertEqual(main('inspect',[str(bytecode),'-o',str(output)]),0)
            self.assertEqual(json.loads(output.read_text()),{'bytecode':code})
            before=output.read_bytes()
            bytecode.write_text('{"magic":"invalid"}')
            diagnostics=io.StringIO()
            with redirect_stderr(diagnostics):
                self.assertEqual(main('inspect',[str(bytecode),'-o',str(output),'--json']),1)
            self.assertIn('error',json.loads(diagnostics.getvalue()))
            self.assertEqual(output.read_bytes(),before)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main('inspect',[str(path),'--stage','ir']),1)

    def test_format_is_native_idempotent_and_preserves_failed_output(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);source=root/'main.pxl';output=root/'formatted.pxl'
            source.write_text('import "missing.pxl" as missing\n// Keep this comment.\neffect fn main(){\nlet table=map[int,string]{1:"one"}\nmissing.use(table)\n}\n')
            with patch('pixellang.text.parse_text', side_effect=AssertionError('Host parser')):
                self.assertEqual(main('format',[str(source),'-o',str(output)]),0)
                formatted=output.read_text()
                self.assertIn('// Keep this comment.',formatted)
                self.assertIn('    let table = map[int, string]',formatted)
                self.assertEqual(main('format',[str(output),'-o',str(output)]),0)
                self.assertEqual(output.read_text(),formatted)
                source.write_text('fn main( {')
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(main('format',[str(source),'-o',str(output),'--json']),1)
                self.assertEqual(output.read_text(),formatted)

    def test_standalone_import_discovery_and_standard_library(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);entry=root/'main.pxl'
            entry.write_text('import "std/collections.pxl" as collections\neffect fn main(){print(collections.Sorted([3,1,2]))}')
            with CompilerClient() as client:
                files,name=client.read_project(entry)
                self.assertIn('std/collections.pxl',files)
                result=client.request('compile',files=files,entry=name)
            from pixellang.vm import VM
            self.assertEqual(VM(result['result']['bytecode']).run(),[[1,2,3]])

    def test_diagnostic_offsets_and_failed_build_preserve_existing_output(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'pixel.toml').write_text('format=1\n[project]\nname="bad"\nentry="main.pxl"\n')
            (root/'main.pxl').write_text('effect fn main(){\nlet value:int="雪"\n}')
            output=root/'old.png';output.write_bytes(b'previous artifact')
            with self.assertRaises(PixelError) as raised:Workspace(root).build(output)
            self.assertEqual(raised.exception.to_dict()['span']['start']['line'],2)
            self.assertEqual(output.read_bytes(),b'previous artifact')
