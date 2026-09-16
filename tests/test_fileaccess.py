import copy
import json
import tempfile
import unittest
from pathlib import Path

from pixellang.fileaccess import FileAccess
from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.temporal import Timeline
from pixellang.vm import VM


class FileCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.read_root = self.root / "read"
        self.write_root = self.root / "write"
        self.read_root.mkdir()
        self.write_root.mkdir()
        (self.read_root / "data.txt").write_text("猫\r\n🌍", encoding="utf-8")
        self.access = FileAccess(self.read_root, self.write_root)

    def machine(self, code, access=None):
        source, compiled = compile_project({"main.pxl": code})
        return source, VM(compiled.bytecode, file_access=access or self.access)

    def test_granted_text_io_and_default_denial(self):
        code = 'import "std/io.pxl" as io\nfn main() {\nlet text = io.Read("data.txt")\nprint(io.Write("report.txt", text))\nprint(text)\n}'
        _, vm = self.machine(code)
        self.assertEqual(vm.run(), [True, "猫\r\n🌍"])
        self.assertEqual(
            (self.write_root / "report.txt").read_bytes(), "猫\r\n🌍".encode()
        )
        _, denied = self.machine(
            'fn main() {\ntry { print(readText("data.txt")) } catch error { print(error.kind) }\n}',
            FileAccess(),
        )
        self.assertEqual(denied.run(), ["io"])

    def test_traversal_symlinks_and_nonregular_files(self):
        secret = self.root / "secret.txt"
        secret.write_text("secret")
        (self.read_root / "link").symlink_to(secret)
        (self.write_root / "link").symlink_to(secret)
        (self.read_root / "directory-link").symlink_to(
            self.root, target_is_directory=True
        )
        for path in [
            "../secret.txt",
            str(secret),
            "link",
            "directory-link/secret.txt",
            ".",
        ]:
            _, vm = self.machine(
                "fn main() { try { print(readText("
                + json.dumps(path)
                + ")) } catch error { print(error.kind) } }",
                FileAccess(self.read_root),
            )
            self.assertEqual(vm.run(), ["io"], path)
        _, vm = self.machine(
            'fn main() {\ntry { writeText("link", "overwrite") } catch error { print(error.kind) }\n}'
        )
        self.assertEqual(vm.run(), ["io"])
        self.assertEqual(secret.read_text(), "secret")
        self.assertTrue((self.write_root / "link").is_symlink())

    def test_replay_uses_captured_reads_and_never_repeats_writes(self):
        source, _ = self.machine(
            'fn main() {\nlet text = readText("data.txt")\nwriteText("out.txt", text)\nprint(text)\n}'
        )
        timeline = Timeline(source, checkpoint_interval=2, file_access=self.access)
        timeline.run()
        document = timeline.document()
        (self.read_root / "data.txt").unlink()
        (self.write_root / "out.txt").write_text("sentinel")
        for z in [5, 2, 8, 1]:
            timeline.seek(z)
        self.assertEqual((self.write_root / "out.txt").read_text(), "sentinel")
        restored = Timeline.restore(document)
        self.assertEqual(restored.vm.state(), timeline.vm.state())
        self.assertEqual(restored.vm.output, ["猫\r\n🌍"])
        self.assertEqual((self.write_root / "out.txt").read_text(), "sentinel")
        self.assertNotIn(str(self.root), json.dumps(document["files"]))

    def test_missing_file_errors_are_also_replayed(self):
        source, _ = self.machine(
            'fn main() {\ntry { print(readText("missing.txt")) } catch error { print("missing") }\n}'
        )
        timeline = Timeline(source, file_access=self.access)
        timeline.run()
        (self.read_root / "missing.txt").write_text("now present")
        self.assertEqual(Timeline.restore(timeline.document()).vm.output, ["missing"])

    def test_budget_failure_precedes_write_and_restores_identically(self):
        source, _ = self.machine(
            'fn main() { writeText("first", "1")\nwriteText("second", "2") }'
        )
        timeline = Timeline(
            source, file_access=FileAccess(write_root=self.write_root, budget=8192)
        )
        timeline.run()
        self.assertIn("budget", timeline.vm.error["message"])
        self.assertTrue((self.write_root / "first").exists())
        self.assertFalse((self.write_root / "second").exists())
        self.assertLessEqual(timeline.file_access.bytes, 8192)
        self.assertEqual(
            Timeline.restore(timeline.document()).vm.state(), timeline.vm.state()
        )

    def test_malformed_or_incomplete_effect_recording_is_rejected(self):
        source, _ = self.machine('fn main() {\nprint(readText("data.txt"))\n}')
        timeline = Timeline(source, file_access=self.access)
        timeline.run()
        document = timeline.document()
        bad = copy.deepcopy(document)
        bad["files"]["effects"] = []
        with self.assertRaises(PixelError):
            Timeline.restore(bad)
        bad = copy.deepcopy(document)
        bad["files"]["effects"][0]["result"] = 42
        with self.assertRaises(PixelError):
            Timeline.restore(bad)

        for key, value in [("request", "g" * 64), ("path", "\ud800")]:
            bad = copy.deepcopy(document)
            bad["files"]["effects"][0][key] = value
            with self.assertRaises(PixelError):
                Timeline.restore(bad)
