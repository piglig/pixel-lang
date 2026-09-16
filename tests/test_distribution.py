import json
from pathlib import Path
import unittest

from scripts.build_distribution import ROOT, identity


class DistributionTests(unittest.TestCase):
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
