import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pixellang.compiler_client import CompilerClient
from pixellang.fileaccess import FileAccess
from pixellang.model import PixelError
from pixellang.temporal import Timeline
from pixellang.vm import VM


class StreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def compile(self, files):
        return self.compiler.request('compile', files=files)['result']['bytecode']

    def test_cli_delimiter_and_repeated_argument_options(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, 'main.pxl')
            source.write_text('effect fn main() { print(programArgs()) }')
            result = subprocess.run([sys.executable, '-m', 'pixellang', 'run', str(source),
                                     '--json', '--arg=first', '--', '--flag', '猫'],
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['output'], [['first', '--flag', '猫']])

    def test_line_separator_across_chunk_boundary_and_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            text = 'a' * 65535 + '\r\n猫🌍\rb\n'
            Path(directory, 'data.txt').write_bytes(text.encode())
            compiled = self.compile({'main.pxl': '''import "std/io.pxl" as io
effect fn main() { match io.ForEachLine("data.txt", effect fn(n, line) { print(n); print(len(line)) }) { .Ok(value) => {}, .Err(error) => print(error) } }'''})
            vm = VM(compiled, file_access=FileAccess(directory, record=False))
            self.assertEqual(vm.run(), [1, 65535, 2, 2, 3, 1, 4, 0])
            self.assertEqual(vm.file_access.effects, [])

    def test_walk_sorted_files_ignores_symlinks_and_replays(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'folder').mkdir()
            (root / 'folder/b.txt').write_text('b')
            (root / 'a.txt').write_text('a')
            (root / 'link').symlink_to(root / 'folder', target_is_directory=True)
            compiled = self.compile({'main.pxl': '''import "std/io.pxl" as io
effect fn main() { match io.Walk("", effect fn(path) { print(path) }) { .Ok(value) => {}, .Err(error) => print(error) } }'''})
            access = FileAccess(directory)
            self.assertEqual(VM(compiled, file_access=access).run(), ['a.txt', 'folder/b.txt'])
            (root / 'a.txt').unlink()
            restored = FileAccess.restore(access.document())
            self.assertEqual(VM(compiled, file_access=restored).run(), ['a.txt', 'folder/b.txt'])
            with self.assertRaises(PixelError):
                FileAccess(directory).perform('listDirectory', '../', None, 0)

    def test_program_arguments_are_copied_and_recorded(self):
        compiled = self.compiler.debug_project({'main.pxl': 'effect fn main() { var values = programArgs(); values.append("changed"); print(values); print(programArgs()) }'})
        values = ['--name', '猫']
        vm = VM(compiled.bytecode, arguments=values)
        values.append('later')
        self.assertEqual(vm.run(), [['--name', '猫', 'changed'], ['--name', '猫']])
        timeline = Timeline(compiled.source, compiled=compiled, arguments=['original'])
        while not timeline.vm.halted:
            timeline.advance()
        restored = Timeline.restore(timeline.document(), compiled=compiled)
        self.assertEqual(restored.vm.output, [['original', 'changed'], ['original']])

    def test_path_and_command_line_rules(self):
        compiled = self.compile({'main.pxl': '''import "std/path.pxl" as path
import "std/cli.pxl" as cli
effect fn main() {
 match path.Join("a/b", "../file.txt") { .Ok(value) => print(value), .Err(error) => print(error) }
 match path.Extension(".hidden") { .Ok(value) => print(value), .Err(error) => print(error) }
 match cli.Parse(programArgs(), ["help"], ["output"]) { .Ok(value) => print(value), .Err(error) => print(error) }
}'''})
        output = VM(compiled, arguments=['--output=a=b', '--', '--file']).run()
        self.assertEqual(output, ['a/file.txt', '', {'options': {'output': 'a=b'}, 'files': ['--file']}])
        rejected = VM(compiled, arguments=['--unknown']).run()
        self.assertEqual(rejected[:2], ['a/file.txt', ''])
        self.assertEqual(rejected[2]['code'], 'cli.unknown_option')
        self.assertIn('Unknown option', rejected[2]['message'])
