import json
import tempfile
import unittest
from pathlib import Path

from pixellang.fileaccess import FileAccess
from pixellang.model import PixelError
from pixellang.temporal import Timeline
from pixellang.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1] / "examples" / "ledger"


class LedgerAcceptanceTests(unittest.TestCase):
    def test_inclusive_upper_bound_and_validation_before_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sales.csv").write_text(
                "category,amount_minor\nbooks,1\nbooks,2\nbooks,3\nbooks,4\n"
                "excluded,invalid\n"
            )
            report = Workspace(ROOT).run(
                json.dumps(
                    {
                        "source": "sales.csv",
                        "output": "report.json",
                        "minimum_minor": 2,
                        "maximum_minor": 3,
                        "categories": ["books"],
                    }
                ),
                file_access=FileAccess(root, root),
            )["output"][0]
            self.assertEqual(
                (report["accepted"], report["rejected"], report["filtered"]), (2, 1, 2)
            )
            self.assertEqual(
                report["groups"],
                [
                    {
                        "category": "books",
                        "count": 2,
                        "total": 5,
                        "min": 2,
                        "max": 3,
                        "mean": 2.5,
                    }
                ],
            )
            self.assertEqual(report["errors"][0]["row"], 6)
            self.assertEqual(json.loads((root / "report.json").read_text()), report)

    def test_contradictory_bounds_fail_before_file_access(self):
        with self.assertRaisesRegex(PixelError, "maximum_minor must be greater"):
            Workspace(ROOT).run(
                json.dumps(
                    {
                        "source": "missing.csv",
                        "output": "report.json",
                        "minimum_minor": 3,
                        "maximum_minor": 2,
                    }
                )
            )

    def test_generated_workload_correctness_and_instruction_bound(self):
        from benchmarks.ledger import fixture

        data, expected = fixture(256)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sales.csv").write_text(data, encoding="utf-8", newline="")
            config = json.dumps(
                {
                    "source": "sales.csv",
                    "output": "report.json",
                    "categories": [],
                    "minimum_minor": 0,
                }
            )
            result = Workspace(ROOT).run(
                config, max_steps=250_000, file_access=FileAccess(root, root)
            )
            self.assertEqual(result["output"], [expected])
            self.assertEqual(json.loads((root / "report.json").read_text()), expected)
            self.assertLess(result["steps"], 250_000)

    def test_optional_configuration_and_fractional_minor_unit_mean(self):
        for optional in [
            {},
            {"categories": None, "minimum_minor": None, "maximum_minor": None},
        ]:
            with (
                self.subTest(optional=optional),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "sales.csv").write_text(
                    "category,amount_minor\nbooks,1\nbooks,2\nbooks,-1\n"
                )
                config = {"source": "sales.csv", "output": "report.json", **optional}
                report = Workspace(ROOT).run(
                    json.dumps(config), file_access=FileAccess(root, root)
                )["output"][0]
                self.assertEqual((report["accepted"], report["filtered"]), (2, 1))
                self.assertEqual(
                    report["groups"],
                    [
                        {
                            "category": "books",
                            "count": 2,
                            "total": 3,
                            "min": 1,
                            "max": 2,
                            "mean": 1.5,
                        }
                    ],
                )

    def test_all_project_fixtures_and_written_reports(self):
        result = Workspace(ROOT).test()
        self.assertTrue(result["passed"], result)
        self.assertEqual(len(result["tests"]), 4)

    def test_real_app_recording_restores_without_files_or_repeated_writes(self):
        source, _ = Workspace(ROOT).compile()
        input_text = (ROOT / "fixtures/config.json").read_text()
        with tempfile.TemporaryDirectory() as directory:
            access = FileAccess(ROOT / "fixtures", directory)
            timeline = Timeline(source, input_text=input_text, file_access=access)
            timeline.run()
            self.assertIsNone(timeline.vm.error)
            actual = json.loads((Path(directory) / "report.json").read_text())
            expected = json.loads((ROOT / "fixtures/expected.json").read_text())[0]
            self.assertEqual(actual, expected)
            document = timeline.document()
            (Path(directory) / "report.json").write_text("sentinel")
            for z in [8000, 1000, 8500]:
                state = timeline.seek(z)
                self.assertIsNone(state["error"])
            restored = Timeline.restore(document)
            self.assertEqual(restored.vm.state(), timeline.vm.state())
            self.assertEqual((Path(directory) / "report.json").read_text(), "sentinel")
        # No directory capability is supplied to restoration.
        self.assertEqual(Timeline.restore(document).vm.output, [expected])

    def test_category_filter_is_applied_after_validation(self):
        config = json.loads((ROOT / "fixtures/config.json").read_text())
        config.update(categories=["books", "food"], minimum_minor=-1_000_000)
        with tempfile.TemporaryDirectory() as directory:
            result = Workspace(ROOT).run(
                json.dumps(config), file_access=FileAccess(ROOT / "fixtures", directory)
            )
        report = result["output"][0]
        self.assertEqual(
            (report["accepted"], report["rejected"], report["filtered"]), (5, 5, 3)
        )
        self.assertEqual(
            [(group["category"], group["total"]) for group in report["groups"]],
            [("books", 1000), ("food", 950)],
        )
