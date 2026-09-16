import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pixellang.onboarding import create_project, diagnose
from pixellang.workspace import Workspace


class OnboardingTests(unittest.TestCase):
    def test_created_project_runs_and_tests(self):
        with TemporaryDirectory() as folder:
            result = create_project(Path(folder) / "hello-pixels")
            workspace = Workspace(result["root"])
            self.assertTrue(workspace.test()["passed"])
            self.assertEqual(workspace.run()["output"], ["Hello, PixelLang!"])

    def test_refuses_existing_content_and_symlink(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "project"
            root.mkdir()
            sentinel = root / "main.pxl"
            sentinel.write_text("keep")
            with self.assertRaises(ValueError):
                create_project(root)
            self.assertEqual(sentinel.read_text(), "keep")
            link = Path(folder) / "link"
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                create_project(link)

    def test_invalid_name_creates_nothing(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "project"
            for name in ('../escape', 'bad"name', '', 'a' * 65):
                with self.assertRaises(ValueError):
                    create_project(root, name)
                self.assertFalse(root.exists())

    def test_doctor_reports_missing_dependency_without_traceback(self):
        result = subprocess.run([sys.executable, "-S", "-m", "pixellang", "doctor", "--json"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        pillow = next(check for check in report["checks"] if check["name"] == "pillow")
        self.assertFalse(pillow["passed"])
        self.assertTrue(pillow["fix"])
        self.assertNotIn("Traceback", result.stderr)

    def test_doctor_checks_real_artifact(self):
        report = diagnose()
        self.assertTrue(report["passed"], report)

    def test_doctor_rejects_corrupted_compiler(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            (artifacts / "compiler.json").write_text("corrupted")
            (artifacts / "compiler-manifest.json").write_text(json.dumps({"sha256": "0" * 64}))
            with patch("pixellang.onboarding.__file__", str(root / "onboarding.py")):
                report = diagnose()
            self.assertFalse(report["passed"])
            compiler = next(check for check in report["checks"] if check["name"] == "compiler")
            self.assertFalse(compiler["passed"])
            self.assertTrue(compiler["fix"])


if __name__ == "__main__":
    unittest.main()
