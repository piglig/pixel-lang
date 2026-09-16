import json
import tempfile
import unittest
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pixellang.fileaccess import FileAccess
from pixellang.model import PixelError
from pixellang.project import compile_project
from pixellang.temporal import Timeline
from pixellang.vm import VM
from pixellang.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1] / "examples/log-analysis"


class LogAnalysisTests(unittest.TestCase):
    def test_project_fixtures(self):
        result = Workspace(ROOT).test()
        self.assertTrue(result["passed"], result)
        self.assertEqual(len(result["tests"]), 3)

    def test_unbounded_optional_times_fractional_mean_and_file_recovery(self):
        for optional in [{}, {"since": None, "until": None}]:
            with (
                self.subTest(optional=optional),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                rows = [
                    {
                        "timestamp": "2026-09-09T10:00:00Z",
                        "level": "INFO",
                        "service": "api",
                        "duration_ms": duration,
                        "message": "ok",
                    }
                    for duration in [1, 2]
                ]
                (root / "events.jsonl").write_text(
                    "\n".join(json.dumps(row) for row in rows)
                )
                config = {
                    "files": ["missing.jsonl", "events.jsonl"],
                    "output": "report.json",
                    **optional,
                }
                report = Workspace(ROOT).run(
                    json.dumps(config), file_access=FileAccess(root, root)
                )["output"][0]
                self.assertEqual(report["count"], 2)
                self.assertEqual(report["duration_mean_ms"], 1.5)
                self.assertEqual(report["problems"][0]["code"], "io.not_found")
                self.assertEqual(report["problems"][0]["source"], "missing.jsonl")

    def test_severity_boundaries_absence_and_validation_before_io(self):
        base = json.loads((ROOT / "fixtures/config.json").read_text())
        for level, count, excluded, total in [
            (None, 4, 2, 81),
            ("INFO", 4, 2, 81),
            ("WARN", 2, 4, 70),
            ("ERROR", 1, 5, 50),
        ]:
            with self.subTest(level=level), tempfile.TemporaryDirectory() as directory:
                report = Workspace(ROOT).run(
                    json.dumps({**base, "minimum_level": level}),
                    file_access=FileAccess(ROOT / "fixtures", directory),
                )["output"][0]
                self.assertEqual(
                    (report["count"], report["excluded"], report["duration_total_ms"]),
                    (count, excluded, total),
                )
                self.assertEqual(len(report["problems"]), 2)
        for invalid in ["DEBUG", "warn", "", True, 1]:
            with self.subTest(invalid=invalid), self.assertRaises(PixelError):
                Workspace(ROOT).run(
                    json.dumps(
                        {
                            "files": ["missing.jsonl"],
                            "output": "report.json",
                            "minimum_level": invalid,
                        }
                    )
                )
        with self.assertRaisesRegex(PixelError, "level must be INFO"):
            Workspace(ROOT).run(
                json.dumps(
                    {
                        "files": ["missing.jsonl"],
                        "output": "report.json",
                        "minimum_level": "DEBUG",
                    }
                )
            )

    def test_generated_multifile_oracle(self):
        origin = datetime(2026, 9, 9, 10, tzinfo=UTC)
        rows = [[], []]
        valid = []
        rejected = excluded = 0
        for index in range(120):
            stamp = origin + timedelta(seconds=index * 7)
            row = {
                "timestamp": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "level": ["INFO", "ERROR", "WARN"][index % 3],
                "service": ["api", "worker"][index % 2],
                "duration_ms": index % 29,
                "message": "日志 " + str(index),
            }
            if index % 17 == 0:
                del row["duration_ms"]
                rejected += 1
            elif stamp >= origin + timedelta(minutes=10):
                excluded += 1
            else:
                valid.append(row)
            rows[index % 2].append(json.dumps(row, ensure_ascii=False))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, lines in enumerate(rows):
                (root / f"{index}.jsonl").write_text("\n".join(lines))
            config = {
                "files": ["0.jsonl", "1.jsonl"],
                "output": "report.json",
                "since": "2026-09-09T10:00:00Z",
                "until": "2026-09-09T10:10:00Z",
            }
            result = Workspace(ROOT).run(
                json.dumps(config),
                max_steps=500_000,
                file_access=FileAccess(root, root),
            )
            report = result["output"][0]
            self.assertEqual(report, json.loads((root / "report.json").read_text()))
            self.assertEqual(report["count"], len(valid))
            self.assertEqual(report["excluded"], excluded)
            self.assertEqual(len(report["problems"]), rejected)
            self.assertEqual(
                report["errors"], sum(r["level"] == "ERROR" for r in valid)
            )
            self.assertEqual(
                report["services"], dict(Counter(r["service"] for r in valid))
            )
            self.assertEqual(report["levels"], dict(Counter(r["level"] for r in valid)))
            self.assertEqual(
                report["minutes"],
                dict(Counter(r["timestamp"][:16] + ":00Z" for r in valid)),
            )
            total = sum(r["duration_ms"] for r in valid)
            self.assertEqual(report["duration_total_ms"], total)
            self.assertEqual(report["duration_mean_ms"], total / len(valid))
            self.assertEqual(
                report["duration_max_ms"], max(r["duration_ms"] for r in valid)
            )

    def test_source_free_recording_replays_without_repeated_output(self):
        source, _ = Workspace(ROOT).compile()
        fixtures = ROOT / "fixtures"
        with tempfile.TemporaryDirectory() as directory:
            timeline = Timeline(
                source,
                input_text=(fixtures / "config.json").read_text(),
                file_access=FileAccess(fixtures, directory),
            )
            timeline.run()
            expected = json.loads((fixtures / "expected.json").read_text())
            self.assertEqual(timeline.vm.output, expected)
            recording = timeline.document()
            output = Path(directory) / "report.json"
            output.write_text("unchanged")
            restored = Timeline.restore(recording)
            self.assertEqual(restored.vm.output, expected)
            for z in (100, 8000, 200, len(restored.events)):
                restored.seek(z)
            self.assertEqual(output.read_text(), "unchanged")

    def test_calendar_validation_including_century_leaps(self):
        files = {
            "main.pxl": 'import "time.pxl" as time\nfn main() { try { time.Validate(input()); print(true) } catch error { print(false) } }',
            "time.pxl": (ROOT / "time.pxl").read_text(),
        }
        _, compiled = compile_project(files)
        for stamp, valid in [
            ("2000-02-29T23:59:59Z", True),
            ("2024-02-29T00:00:00Z", True),
            ("1900-02-29T00:00:00Z", False),
            ("2026-02-29T00:00:00Z", False),
            ("2026-09-09T24:00:00Z", False),
            ("0000-01-01T00:00:00Z", False),
            ("2026-09-09T00:00:00+09:00", False),
        ]:
            with self.subTest(stamp=stamp):
                self.assertEqual(VM(compiled.bytecode, input_text=stamp).run(), [valid])
