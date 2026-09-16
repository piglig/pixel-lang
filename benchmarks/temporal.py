"""Run from repository root: python -m benchmarks.temporal --iterations 3000."""

import argparse
import json
import platform
import random
import statistics
import time
import tracemalloc

from pixellang.project import compile_project
from pixellang.temporal import Timeline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=3000)
    parser.add_argument("-o", "--output")
    parser.add_argument("--random-seeks", action="store_true")
    parser.add_argument("--closures", action="store_true")
    parser.add_argument("--fifo-baseline", action="store_true")
    args = parser.parse_args()
    code = f"fn main() {{\nvar i = 0\nwhile i < {args.iterations} {{ let scratch = [i, i + 1, i + 2]; i += 1 }}\nprint(i)\n}}"
    if args.closures:
        code = f"""fn main() {{
 var total = 0
 let add = fn(value: int) {{ total += value }}
 var i = 0
 while i < {args.iterations} {{
  let next = fn() {{ add(1) }}
  next()
  i += 1
 }}
 print(total)
}}"""
    source, _ = compile_project({"main.pxl": code})
    tracemalloc.start()
    if args.fifo_baseline:
        from benchmarks.ledger_replay import FifoBaseline

        timeline = FifoBaseline(source, max_steps=1_000_000)
    else:
        timeline = Timeline(source, max_steps=1_000_000)
    started = time.perf_counter()
    timeline.run()
    elapsed = time.perf_counter() - started
    assert timeline.vm.error is None, timeline.vm.error
    assert timeline.vm.output == [args.iterations]
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    seeks, replay_counts = [], []
    rng = random.Random(42)
    for i in range(100):
        z = (
            rng.randrange(1, len(timeline.events))
            if args.random_seeks
            else ((i * 7919) % (len(timeline.events) - 1)) + 1
        )
        if args.random_seeks:
            timeline.replay_vm = None  # cold replay cache; retained checkpoints remain
            timeline.replay_z = 0
        started = time.perf_counter()
        timeline.seek(z)
        seeks.append((time.perf_counter() - started) * 1000)
        replay_counts.append(timeline.last_seek_replayed)
    report = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "iterations": args.iterations,
        "workload": "closures" if args.closures else "arrays",
        "checkpoint_policy": "fifo-baseline" if args.fifo_baseline else "spread",
        "correct": True,
        "transitions": len(timeline.events),
        "recording_seconds_with_tracemalloc": elapsed,
        "python_peak_bytes": peak,
        "journal_budgeted_bytes": timeline.events.bytes,
        "checkpoint_budgeted_bytes": timeline.checkpoint_bytes,
        "retained_checkpoints": len(timeline.checkpoints),
        "heap_live_arrays": len(timeline.vm.heap),
        "gc_collections": timeline.vm.collections,
        "seek_mode": "seeded-random-cold"
        if args.random_seeks
        else "deterministic-warm",
        "seek_max_ms": max(seeks),
        "seek_median_ms": statistics.median(seeks),
        "seek_p95_ms": sorted(seeks)[94],
        "seek_max_replayed_instructions": max(replay_counts),
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(text + "\n")


if __name__ == "__main__":
    main()
