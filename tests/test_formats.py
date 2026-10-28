import copy
import unittest

from pixellang.model import VERSIONS, PixelError
from pixellang.compiler_client import CompilerClient
from pixellang.temporal import Timeline
from pixellang.vm import VM


class FormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerClient()
        cls.addClassCleanup(cls.compiler.close)

    def setUp(self):
        self.compiled = self.compiler.debug_project(
            {"main.pxl": "effect fn main() { print(42) }"}
        )

        # Build is intentionally text-only. Format tests explicitly request the
        # spatial document whose version axes they mutate below.
        self.source = self.compiler.request(
            'debug-pixels', **self.compiled.source_project
        )['result']['document']
        self.compiled.source = self.source

    def test_current_version_roundtrip(self):
        self.assertEqual(self.source["versions"], VERSIONS)
        self.assertEqual(set(VERSIONS.values()), {"0.9"})
        self.assertEqual(self.compiled.bytecode["vm_version"], "0.9")
        image = self.compiler.request('export-png', **self.compiled.source_project)['result']['image']
        decoded = self.compiler.request('recover-png', image=image)['result']
        rebuilt = self.compiler.debug_project(**decoded)
        self.assertEqual(VM(rebuilt.bytecode).run(), [42])
        timeline = Timeline(rebuilt.source, compiled=rebuilt)
        timeline.run()
        self.assertEqual(timeline.document()["version"], "1.0")
        self.assertEqual(Timeline.restore(timeline.document()).vm.output, [42])

    def test_exactly_one_bytecode_entry_is_required(self):
        for entry in ([], self.compiled.bytecode["entry"] * 2):
            bytecode = copy.deepcopy(self.compiled.bytecode)
            bytecode["entry"] = entry
            with self.assertRaises(PixelError):
                VM(bytecode)

    def test_all_old_version_axes_rejected(self):
        for old in ("0.1", "0.2", "0.3", "0.4", "0.7", "0.8", "99.0"):
            for axis in VERSIONS:
                with self.subTest(old=old, axis=axis), self.assertRaises(PixelError):
                    source = copy.deepcopy(self.source)
                    source["versions"][axis] = old
                    self.compiler.request('compile-pixels', document=source)
            with self.subTest(old=old, layer="bytecode"), self.assertRaises(PixelError):
                bytecode = copy.deepcopy(self.compiled.bytecode)
                bytecode.update(vm_version=old, language_version=old)
                VM(bytecode)
            with self.subTest(old=old, layer="bundle"), self.assertRaises(PixelError):
                source = copy.deepcopy(self.source)
                source["bundle"]["version"] = old
                self.compiler.request('compile-pixels', document=source)
            timeline = Timeline(self.source, compiled=self.compiled)
            timeline.run()
            document = timeline.document()
            document["version"] = old
            with self.subTest(old=old, layer="timeline"), self.assertRaises(PixelError):
                Timeline.restore(document)

    def test_malformed_runtime_schema_and_iteration_mode_rejected(self):
        result = self.compiler.debug_project(
            {"main.pxl": "effect fn main() { for i, v in [42] { print(v) } }"}
        )
        bytecode = copy.deepcopy(result.bytecode)
        bytecode["records"]["Error"]["message"] = "int"
        with self.assertRaises(PixelError):
            VM(bytecode)
        for mode in (None, "legacy", 1, [], {}):
            bytecode = copy.deepcopy(result.bytecode)
            for function in bytecode["functions"].values():
                for instruction in function["instructions"]:
                    if instruction["op"] == "ITER_SNAPSHOT":
                        instruction["arg"] = mode
            with self.subTest(mode=mode), self.assertRaises(PixelError):
                VM(bytecode)
