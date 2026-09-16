from contextlib import redirect_stdout
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
    def test_source_and_source_free_png_run_through_service(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'pixel.toml').write_text('format=1\n[project]\nname="cli"\nentry="main.pxl"\n')
            (root/'main.pxl').write_text('import "lib.pxl" as lib\nfn main(){print(lib.value())}')
            (root/'lib.pxl').write_text('export fn value()->int=42')
            with patch('pixellang.cli.compile_file',side_effect=AssertionError('Host file compiler')),patch(
                'pixellang.project.compile_project',side_effect=AssertionError('Host project compiler')):
                output=io.StringIO()
                with redirect_stdout(output):status=main('run',[str(root/'main.pxl'),'--json'])
                self.assertEqual(status,0,output.getvalue())
                self.assertEqual(json.loads(output.getvalue())['output'],[42])
                result=Workspace(root).build()
                (root/'main.pxl').unlink();(root/'lib.pxl').unlink()
                recovered=root/'recovered'
                output=io.StringIO()
                with patch('pixellang.project.project_text',side_effect=AssertionError('Host image recovery')):
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

    def test_standalone_import_discovery_and_standard_library(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);entry=root/'main.pxl'
            entry.write_text('import "std/sort.pxl" as sort\nfn main(){print(sort.Ints([3,1,2]))}')
            with CompilerClient() as client,patch('pixellang.project.parse_text',side_effect=AssertionError('Host parser')):
                files,name=client.read_project(entry)
                self.assertIn('std/sort.pxl',files)
                result=client.request('compile',files=files,entry=name)
            from pixellang.vm import VM
            self.assertEqual(VM(result['result']['bytecode']).run(),[[1,2,3]])

    def test_diagnostic_offsets_and_failed_build_preserve_existing_output(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'pixel.toml').write_text('format=1\n[project]\nname="bad"\nentry="main.pxl"\n')
            (root/'main.pxl').write_text('fn main(){\nlet value:int="雪"\n}')
            output=root/'old.png';output.write_bytes(b'previous artifact')
            with self.assertRaises(PixelError) as raised:Workspace(root).build(output)
            self.assertEqual(raised.exception.to_dict()['span']['start']['line'],2)
            self.assertEqual(output.read_bytes(),b'previous artifact')
