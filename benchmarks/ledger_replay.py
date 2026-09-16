"""Real multi-file Ledger recording/replay, with an explicit historical baseline."""

import argparse
import json
import platform
import random
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

from benchmarks.ledger import fixture
from pixellang.fileaccess import FileAccess
from pixellang.recording import footprint
from pixellang.temporal import Timeline
from pixellang.workspace import Workspace


class FifoBaseline(Timeline):
    """0.6 eviction algorithm, retained only for reproducible comparison."""

    def _save_checkpoint(self, z):
        snapshot = self.vm.checkpoint()
        size = footprint(snapshot)
        if size > self.checkpoint_budget:
            return
        while (
            self.checkpoints and self.checkpoint_bytes + size > self.checkpoint_budget
        ):
            _, removed = self.checkpoints.pop(next(iter(self.checkpoints)))
            self.checkpoint_bytes -= removed
        self.checkpoints[z] = (snapshot, size)
        self.checkpoint_bytes += size


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--fifo-baseline", action="store_true")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    source, _ = Workspace(
        Path(__file__).resolve().parents[1] / "examples/ledger"
    ).compile()
    csv, expected = fixture(args.rows)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "sales.csv").write_text(csv)
        config = json.dumps(
            {"source": "sales.csv", "output": "report.json", "minimum_minor": 0}
        )
        tracemalloc.start()
        timeline = (FifoBaseline if args.fifo_baseline else Timeline)(
            source,
            input_text=config,
            max_steps=1_000_000,
            file_access=FileAccess(root, root),
        )
        started = time.perf_counter()
        timeline.run()
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert timeline.vm.error is None, timeline.vm.error
        assert timeline.vm.output == [expected]
        assert json.loads((root / "report.json").read_text()) == expected
        (root / "report.json").write_text("must not be rewritten")
        (root / "sales.csv").unlink()
        rng = random.Random(42)
        seeks, replay_counts = [], []
        for _ in range(100):
            z = rng.randrange(1, len(timeline.events))
            timeline.replay_vm = None
            timeline.replay_z = 0
            started = time.perf_counter()
            timeline.seek(z)
            seeks.append((time.perf_counter() - started) * 1000)
            replay_counts.append(timeline.last_seek_replayed)
        restored = Timeline.restore(timeline.document())
        assert restored.vm.output == [expected]
        assert (root / "report.json").read_text() == "must not be rewritten"
        report = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "workload": "ledger",
            "rows": args.rows,
            "correct": True,
            "replay_without_input_files_or_repeated_writes": True,
            "checkpoint_policy": "fifo-baseline" if args.fifo_baseline else "spread",
            "transitions": len(timeline.events),
            "recording_seconds_with_tracemalloc": elapsed,
            "python_peak_bytes": peak,
            "journal_budgeted_bytes": timeline.events.bytes,
            "checkpoint_budgeted_bytes": timeline.checkpoint_bytes,
            "retained_checkpoints": len(timeline.checkpoints),
            "seek_mode": "100 seeded-random-cold",
            "seek_median_ms": statistics.median(seeks),
            "seek_p95_ms": sorted(seeks)[94],
            "seek_max_ms": max(seeks),
            "seek_max_replayed_instructions": max(replay_counts),
        }
        Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
