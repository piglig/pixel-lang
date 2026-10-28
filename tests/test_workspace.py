import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from pixellang.compiler_client import CompilerClient
from pixellang.model import PixelError
from pixellang.vm import VM
from pixellang.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "app"
        self.root.mkdir()
        self.write(
            "pixel.toml",
            'format = 1\n[project]\nname = "Example"\nentry = "main.pxl"\n',
        )
        self.write("main.pxl", "effect fn main() {\nprint(42)\n}")

    def write(self, name, content):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def dependency(self):
        dep = self.base / "math"
        dep.mkdir()
        (dep / "pixel.toml").write_text('format = 1\n[project]\nname = "math"\n')
        (dep / "main.pxl").write_text("export fn Add(x: int) -> int = x + 2")
        with (self.root / "pixel.toml").open("a") as f:
            f.write('[dependencies]\nmath = "../math"\n')
        self.write(
            "main.pxl",
            'import "deps/math/main.pxl" as math\neffect fn main() {\nprint(math.Add(40))\n}',
        )
        return dep

    def test_studio_workspace_paths_and_lock_gate(self):
        from pixellang.workstation import Workstation

        dep = self.dependency()
        service = Workstation()
        self.addCleanup(service.close)
        project = service.request("workspace", {"root": str(self.root)})
        self.assertEqual(
            project["paths"]["deps/math/main.pxl"], str((dep / "main.pxl").resolve())
        )
        self.assertIn("not locked", project["lock_error"])
        with self.assertRaises(PixelError):
            service.request("build", project)
        Workspace(self.root).lock()
        project = service.request("workspace", {"root": str(self.root)})
        self.assertIsNone(project["lock_error"])
        self.assertIn("session", service.request("build", project))

    def test_dependency_lock_detects_source_and_manifest_changes(self):
        dep = self.dependency()
        with self.assertRaisesRegex(PixelError, "not locked"):
            Workspace(self.root).run()
        Workspace(self.root).lock()
        self.assertEqual(Workspace(self.root).run()["output"], [42])
        for filename in ["main.pxl", "pixel.toml"]:
            path = dep / filename
            original = path.read_text()
            path.write_text(original + "\n")
            with self.assertRaisesRegex(PixelError, "differ"):
                Workspace(self.root).run()
            path.write_text(original)
        original = (dep / "main.pxl").read_bytes()
        (dep / "main.pxl").write_bytes(original + b"\r\n")
        with self.assertRaisesRegex(PixelError, "differ"):
            Workspace(self.root).run()

    def test_determinism_relocation_and_source_free_image_execution(self):
        self.dependency()
        workspace = Workspace(self.root)
        workspace.lock()
        first = workspace.build()
        relocated = self.base / "elsewhere"
        shutil.copytree(self.root, relocated / "app")
        shutil.copytree(self.base / "math", relocated / "math")
        second = Workspace(relocated / "app").build()
        self.assertEqual(first["sha256"], second["sha256"])
        image = self.base / "standalone.png"
        shutil.copy(first["image"], image)
        shutil.rmtree(self.root)
        shutil.rmtree(self.base / "math")
        with CompilerClient() as compiler:
            bytecode = compiler.request('compile-png', image=image.read_bytes())['result']['bytecode']
        self.assertEqual(VM(bytecode).run(), [42])

    def test_test_discovery_fixtures_and_failure_exit_status(self):
        self.write("tests/ok_test.pxl", "effect fn main() { assert(6 * 7 == 42) }")
        self.write("tests/input.pxl", "effect fn main() {\nprint(input())\n}")
        self.write("fixtures/input.txt", "你好\n")
        self.write("fixtures/output.json", json.dumps(["你好\n"]))
        with (self.root / "pixel.toml").open("a") as f:
            f.write(
                '[[test]]\nname = "Unicode input"\nentry = "tests/input.pxl"\ninput = "fixtures/input.txt"\nexpected = "fixtures/output.json"\n'
            )
        results = Workspace(self.root).test()
        self.assertTrue(results["passed"])
        self.assertEqual(len(results["tests"]), 2)
        self.write("tests/bad_test.pxl", "effect fn main() { assert(false) }")
        command = subprocess.run(
            [sys.executable, "-m", "pixellang", "test", str(self.root), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(command.returncode, 1, command.stderr)
        self.assertEqual(
            [r["passed"] for r in json.loads(command.stdout)["tests"]],
            [True, False, True],
        )

    def test_no_tests_is_not_a_success(self):
        with self.assertRaisesRegex(PixelError, "No tests"):
            Workspace(self.root).test()

    def test_function_discovery_filter_and_failure_isolation(self):
        self.write("tests/arithmetic_test.pxl", """fn test_pass() { assert(6 * 7 == 42) }
fn test_fail() { assert(false) }
fn helper() {}""")
        workspace = Workspace(self.root)
        listed = workspace.test(list_only=True)
        self.assertEqual([item["function"] for item in listed["tests"]], ["test_pass", "test_fail"])
        self.assertTrue(workspace.test(pattern="*::test_pass")["passed"])
        results = workspace.test()
        self.assertEqual([item["passed"] for item in results["tests"]], [True, False])
        self.assertIn("diagnostic", results["tests"][1])
        diagnostic = results["tests"][1]["diagnostic"]
        self.assertEqual(diagnostic["span"]["source"], "tests/arithmetic_test.pxl")
        self.assertEqual(diagnostic["span"]["start"]["line"], 2)
        self.assertNotIn("fn main", (self.root / "tests/arithmetic_test.pxl").read_text())
        with self.assertRaisesRegex(PixelError, "No tests match"):
            workspace.test(pattern="*missing*")

    def test_invalid_test_function_signature_is_reported(self):
        self.write("tests/invalid_test.pxl", "fn test_bad(value: int) {}")
        result = Workspace(self.root).test()
        self.assertFalse(result["passed"])
        self.assertIn("take no parameters", result["tests"][0]["error"])

    def test_native_discovery_and_entry_do_not_parse_or_rewrite_source(self):
        from pixellang.compiler_client import CompilerClient
        source = '''frame World = meters
fn Origin() -> Point3[World] = Point3::[World]{x: 0.0, y: 0.0, z: 3.0}
export fn test_native() { assert(Origin().z == 3.0) }
fn helper() { let test_nested = fn() {} }
'''
        self.write("tests/native_test.pxl", source)
        original_request = CompilerClient.request
        entries = []
        def request(client, operation, **kwargs):
            if operation == "compile":
                self.assertEqual(kwargs["files"]["tests/native_test.pxl"], source)
                entries.append(kwargs.get("entryFunction"))
            return original_request(client, operation, **kwargs)
        with patch("pixellang.text.parse_text", side_effect=AssertionError("Host parser used")), patch.object(
                CompilerClient, "request", request), patch.dict("os.environ", {"PIXELLANG_DISABLE_CACHE": "1"}):
            workspace = Workspace(self.root)
            self.assertEqual([case["function"] for case in workspace.tests], ["test_native"])
            self.assertTrue(workspace.test()["passed"])
        self.assertEqual(entries, ["test_native"])
        self.assertEqual((self.root / "tests/native_test.pxl").read_text(), source)

    def test_discovery_is_lazy_and_transport_failure_can_be_retried(self):
        from pixellang.compiler_client import CompilerClient
        self.write("tests/a_test.pxl", "fn test_a() {}")
        self.write("tests/b_test.pxl", "fn test_b() {}")
        with patch.object(CompilerClient, "request", side_effect=AssertionError("unexpected discovery")):
            workspace = Workspace(self.root)
            workspace.lock()
        count = 0
        def interrupted(client, operation, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise PixelError("service", "Discovery interrupted", code="service.cancelled")
            return {"result": {"tests": [{"name": "test_a", "valid": True}], "hasMain": False}}
        with patch.object(CompilerClient, "request", interrupted):
            with self.assertRaisesRegex(PixelError, "Discovery interrupted"):
                _ = workspace.tests
        self.assertEqual(workspace._tests, [])
        self.assertEqual([case["function"] for case in workspace.tests], ["test_a", "test_b"])
        with patch.object(CompilerClient, "request", side_effect=AssertionError("repeated discovery")):
            self.assertEqual(len(workspace.tests), 2)

    def test_native_discovery_keeps_invalid_signatures_and_syntax_isolated(self):
        self.write("tests/signatures_test.pxl", '''fn test_good() -> unit {}
fn test_generic[T]() {}
fn test_value() -> int = 1
fn test_argument(value: int) {}
''')
        self.write("tests/broken_test.pxl", "fn test_broken( {")
        workspace = Workspace(self.root)
        self.assertTrue(workspace.test(pattern="*::test_good")["passed"])
        result = workspace.test()
        self.assertFalse(result["passed"])
        self.assertEqual(sum(item["passed"] for item in result["tests"]), 1)
        self.assertEqual(len(result["tests"]), 5)

    def test_exact_selection_distinguishes_duplicate_names(self):
        self.write("tests/pass.pxl", "effect fn main() { assert(true) }")
        self.write("tests/fail.pxl", "effect fn main() { assert(false) }")
        with (self.root / "pixel.toml").open("a") as stream:
            for entry in ("pass", "fail"):
                stream.write(f'[[test]]\nname = "duplicate"\nentry = "tests/{entry}.pxl"\n')
        workspace = Workspace(self.root)
        listed = workspace.test(list_only=True)["tests"]
        self.assertEqual(len(listed), 2)
        self.assertNotEqual(listed[0]["id"], listed[1]["id"])
        for case in listed:
            result = workspace.test(test_id=case["id"])
            self.assertEqual(len(result["tests"]), 1)
            self.assertEqual(result["passed"], case["entry"] == "tests/pass.pxl")
        with self.assertRaisesRegex(PixelError, "No tests match"):
            workspace.test(test_id="stale-id")

    def test_paths_and_symlinks_cannot_escape_implicit_sources(self):
        outside = self.base / "outside.pxl"
        outside.write_text("effect fn main() {\nprint(100)\n}")
        (self.root / "escape.pxl").symlink_to(outside)
        with self.assertRaisesRegex(PixelError, "symlink"):
            Workspace(self.root)

    def test_cyclic_dependency_is_rejected(self):
        dep = self.dependency()
        with (dep / "pixel.toml").open("a") as f:
            f.write('[dependencies]\napp = "../app"\n')
        with self.assertRaisesRegex(PixelError, "Cyclic"):
            Workspace(self.root)

    def test_nested_dependency_namespaces(self):
        dep = self.dependency()
        nested = self.base / "numbers"
        nested.mkdir()
        (nested / "pixel.toml").write_text('format = 1\n[project]\nname = "numbers"\n')
        (nested / "main.pxl").write_text("export fn Two() -> int = 2")
        (dep / "main.pxl").write_text(
            'import "deps/numbers/main.pxl" as numbers\nexport fn Add(x: int) -> int = x + numbers.Two()'
        )
        with (dep / "pixel.toml").open("a") as f:
            f.write('[dependencies]\nnumbers = "../numbers"\n')
        workspace = Workspace(self.root)
        workspace.lock()
        self.assertEqual(workspace.run()["output"], [42])
        self.assertEqual(len(workspace.dependencies), 2)
