import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pixellang.compiler import compile_file
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
        self.write("main.pxl", "fn main() {\nprint(42)\n}")

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
            'import "deps/math/main.pxl" as math\nfn main() {\nprint(math.Add(40))\n}',
        )
        return dep

    def test_studio_workspace_paths_and_lock_gate(self):
        from pixellang.workstation import Workstation

        dep = self.dependency()
        service = Workstation()
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
        self.assertEqual(VM(compile_file(image).bytecode).run(), [42])

    def test_test_discovery_fixtures_and_failure_exit_status(self):
        self.write("tests/ok_test.pxl", "fn main() { assert(6 * 7 == 42) }")
        self.write("tests/input.pxl", "fn main() {\nprint(input())\n}")
        self.write("fixtures/input.txt", "你好\n")
        self.write("fixtures/output.json", json.dumps(["你好\n"]))
        with (self.root / "pixel.toml").open("a") as f:
            f.write(
                '[[test]]\nname = "Unicode input"\nentry = "tests/input.pxl"\ninput = "fixtures/input.txt"\nexpected = "fixtures/output.json"\n'
            )
        results = Workspace(self.root).test()
        self.assertTrue(results["passed"])
        self.assertEqual(len(results["tests"]), 2)
        self.write("tests/bad_test.pxl", "fn main() { assert(false) }")
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

    def test_paths_and_symlinks_cannot_escape_implicit_sources(self):
        outside = self.base / "outside.pxl"
        outside.write_text("fn main() {\nprint(100)\n}")
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
