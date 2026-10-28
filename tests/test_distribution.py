import json
from pathlib import Path
import unittest
import tempfile
import zipfile

from scripts.build_distribution import ROOT, identity, verify_tree


class DistributionTests(unittest.TestCase):
    def test_archive_rejects_removed_runtime_module(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "current.py").write_text("current = True\n")
            with zipfile.ZipFile(root / "package.zip", "w") as archive:
                archive.writestr("pixellang/current.py", "current = True\n")
                verify_tree(archive, source, "pixellang/", ("*.py",))
                archive.writestr("pixellang/removed.py", "obsolete = True\n")
                with self.assertRaisesRegex(ValueError, "Archive contains removed pixellang/removed.py"):
                    verify_tree(archive, source, "pixellang/", ("*.py",))

    def test_release_metadata_matches_runtime_and_compiler(self):
        release = identity()
        self.assertEqual(release["license"], "Apache-2.0")
        self.assertTrue(release["prerelease"])
        self.assertEqual(len(release["compilerSha256"]), 64)

    def test_extension_onboarding_commands_declared(self):
        package = json.loads((ROOT / "vscode/package.json").read_text())
        commands = {item["command"] for item in package["contributes"]["commands"]}
        self.assertTrue({"pixellang.createProject", "pixellang.doctor", "pixellang.selectPython"} <= commands)


if __name__ == "__main__":
    unittest.main()
