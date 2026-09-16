import copy
import unittest

from pixellang.compiler import compile_source
from pixellang.model import VERSIONS, PixelError
from pixellang.picture import decode_picture, encode_picture
from pixellang.project import compile_project
from pixellang.temporal import Timeline
from pixellang.vm import VM


class FormatV5Tests(unittest.TestCase):
    def setUp(self):
        self.source, self.compiled = compile_project(
            {"main.pxl": "fn main() { print(42) }"}
        )

    def test_current_version_roundtrip(self):
        self.assertEqual(self.source["versions"], VERSIONS)
        self.assertEqual(set(VERSIONS.values()), {"0.8"})
        self.assertEqual(self.compiled.bytecode["vm_version"], "0.8")
        decoded = decode_picture(encode_picture(self.source))
        self.assertEqual(VM(compile_source(decoded).bytecode).run(), [42])
        timeline = Timeline(decoded)
        timeline.run()
        self.assertEqual(timeline.document()["version"], "0.8")
        self.assertEqual(Timeline.restore(timeline.document()).vm.output, [42])

    def test_exactly_one_bytecode_entry_is_required(self):
        for entry in ([], self.compiled.bytecode["entry"] * 2):
            bytecode = copy.deepcopy(self.compiled.bytecode)
            bytecode["entry"] = entry
            with self.assertRaises(PixelError):
                VM(bytecode)

    def test_all_old_version_axes_rejected(self):
        for old in ("0.1", "0.2", "0.3", "0.4", "99.0"):
            for axis in VERSIONS:
                with self.subTest(old=old, axis=axis), self.assertRaises(PixelError):
                    source = copy.deepcopy(self.source)
                    source["versions"][axis] = old
                    compile_source(source)
            with self.subTest(old=old, layer="bytecode"), self.assertRaises(PixelError):
                bytecode = copy.deepcopy(self.compiled.bytecode)
                bytecode.update(vm_version=old, language_version=old)
                VM(bytecode)
            with self.subTest(old=old, layer="bundle"), self.assertRaises(PixelError):
                source = copy.deepcopy(self.source)
                source["bundle"]["version"] = old
                compile_source(source)
            timeline = Timeline(self.source)
            timeline.run()
            document = timeline.document()
            document["version"] = old
            with self.subTest(old=old, layer="timeline"), self.assertRaises(PixelError):
                Timeline.restore(document)

    def test_malformed_runtime_schema_and_iteration_mode_rejected(self):
        _, result = compile_project(
            {"main.pxl": "fn main() { for i, v in [42] { print(v) } }"}
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
