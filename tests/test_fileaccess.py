import copy
import json
import tempfile
import unittest
from pathlib import Path

from pixellang.fileaccess import FileAccess
from pixellang.model import PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM


class FileCapabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def test_chunk_reads_preserve_unicode_and_replay_without_disk(self):
        text = "a猫🌍\r\nb尾"
        (self.read_root / "chunks.txt").write_bytes(text.encode("utf-8"))
        access = FileAccess(self.read_root)
        offset, pieces, requests = 0, [], []
        while True:
            requests.append([offset, 4])
            value = access.perform("readChunk", "chunks.txt", requests[-1], len(requests) - 1)
            pieces.append(value["text"])
            if value["done"]:
                break
            self.assertGreater(value["next"], offset)
            offset = value["next"]
        self.assertEqual("".join(pieces), text)
        (self.read_root / "chunks.txt").unlink()
        restored = FileAccess.restore(access.document())
        self.assertEqual("".join(restored.perform("readChunk", "chunks.txt", request, i)["text"]
                                 for i, request in enumerate(requests)), text)

    def test_unrecorded_stream_does_not_retain_observations(self):
        (self.read_root / "chunks.txt").write_text("abcdef")
        access = FileAccess(self.read_root, budget=8192, record=False)
        for i in range(100):
            self.assertEqual(access.perform("readChunk", "chunks.txt", [0, 4], i)["text"], "abcd")
        self.assertEqual(access.effects, [])
        self.assertEqual(access.bytes, 0)
        with self.assertRaisesRegex(PixelError, "not recorded"):
            access.document()

    def test_unrecorded_compiler_access_keeps_aggregate_budget(self):
        (self.read_root / "data.txt").write_text("abc")
        access = FileAccess(self.read_root, budget=16384, record=False, limit_total=True)
        self.assertEqual(access.perform("read", "data.txt", None, 0), "abc")
        self.assertEqual(access.perform("read", "data.txt", None, 1), "abc")
        with self.assertRaisesRegex(PixelError, "budget exceeded"):
            access.perform("read", "data.txt", None, 2)
        self.assertEqual(access.effects, [])

    def test_chunk_errors_are_replayable(self):
        (self.read_root / "bad.txt").write_bytes(b"abc\xff")
        access = FileAccess(self.read_root)
        with self.assertRaises(PixelError) as raised:
            access.perform("readChunk", "bad.txt", [0, 4], 0)
        self.assertEqual(raised.exception.code, "io.invalid_encoding")
        (self.read_root / "bad.txt").unlink()
        with self.assertRaises(PixelError) as raised:
            FileAccess.restore(access.document()).perform("readChunk", "bad.txt", [0, 4], 0)
        self.assertEqual(raised.exception.code, "io.invalid_encoding")

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
        compiled = self.compiler.debug_project({"main.pxl": code})
        return compiled, VM(compiled.bytecode, file_access=access or self.access)

    def test_granted_text_io_and_default_denial(self):
        code = 'import "std/io.pxl" as io\neffect fn main() {\nmatch io.Read("data.txt") { .Ok(text)=>{\nmatch io.Write("report.txt", text) { .Ok(written)=>{print(written)}, .Err(error)=>{print(error.kind)} }\nprint(text)\n}, .Err(error)=>{print(error.kind)} }\n}'
        _, vm = self.machine(code)
        self.assertEqual(vm.run(), [True, "猫\r\n🌍"])
        self.assertEqual(
            (self.write_root / "report.txt").read_bytes(), "猫\r\n🌍".encode()
        )
        _, denied = self.machine(
            'effect fn main() {\nmatch readText("data.txt") { .Ok(text)=>{print(text)}, .Err(error)=>{print(error.kind)} }\n}',
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
                "effect fn main() { match readText("
                + json.dumps(path)
                + ") { .Ok(text)=>{print(text)}, .Err(error)=>{print(error.kind)} } }",
                FileAccess(self.read_root),
            )
            self.assertEqual(vm.run(), ["io"], path)
        _, vm = self.machine(
            'effect fn main() {\nmatch writeText("link", "overwrite") { .Ok(written)=>{print(written)}, .Err(error)=>{print(error.kind)} }\n}'
        )
        self.assertEqual(vm.run(), ["io"])
        self.assertEqual(secret.read_text(), "secret")
        self.assertTrue((self.write_root / "link").is_symlink())

    def test_replay_uses_captured_reads_and_never_repeats_writes(self):
        compiled, _ = self.machine(
            'effect fn main() {\nmatch readText("data.txt") { .Ok(text)=>{\nmatch writeText("out.txt", text) { .Ok(written)=>{assert(written)}, .Err(error)=>{print(error.kind)} }\nprint(text)\n}, .Err(error)=>{print(error.kind)} }\n}'
        )
        timeline = Timeline(compiled.source, compiled=compiled, checkpoint_interval=2, file_access=self.access)
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
        compiled, _ = self.machine(
            'effect fn main() {\nmatch readText("missing.txt") { .Ok(text)=>{print(text)}, .Err(error)=>{print("missing")} }\n}'
        )
        timeline = Timeline(compiled.source, compiled=compiled, file_access=self.access)
        timeline.run()
        (self.read_root / "missing.txt").write_text("now present")
        self.assertEqual(Timeline.restore(timeline.document()).vm.output, ["missing"])

    def test_budget_failure_precedes_write_and_restores_identically(self):
        compiled, _ = self.machine(
            'effect fn main() { let first = writeText("first", "1")\nlet second = writeText("second", "2") }'
        )
        timeline = Timeline(
            compiled.source, compiled=compiled, file_access=FileAccess(write_root=self.write_root, budget=8192)
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
        compiled, _ = self.machine('effect fn main() {\nmatch readText("data.txt") { .Ok(text)=>{print(text)}, .Err(error)=>{print(error.kind)} }\n}')
        timeline = Timeline(compiled.source, compiled=compiled, file_access=self.access)
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
