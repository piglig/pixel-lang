"""Real CSV workload: python -m benchmarks.ledger --rows 1000 -o report.json."""

import argparse
import csv
import io
import json
import platform
import tempfile
import time
import tracemalloc
from pathlib import Path

from pixellang.fileaccess import FileAccess
from pixellang.vm import VM
from pixellang.workspace import Workspace


def fixture(count):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["category", "amount_minor"])
    categories = [
        "books",
        "art",
        "食品",
        "home,office",
        "two\nlines",
        "travel",
        "games",
        "music",
    ]
    grouped, errors, filtered = {}, [], 0
    for i in range(count):
        category, amount = categories[i % len(categories)], i % 201 - 100
        if i % 97 == 0:
            writer.writerow(["", amount])
            errors.append({"row": i + 2, "message": "Category cannot be empty"})
        else:
            writer.writerow([category, amount])
            if amount < 0:
                filtered += 1
            else:
                grouped.setdefault(category, []).append(amount)
    groups = [
        {
            "category": key,
            "count": len(values),
            "total": sum(values),
            "min": min(values),
            "max": max(values),
            "mean": sum(values) / len(values),
        }
        for key, values in grouped.items()
    ]
    groups.sort(key=lambda group: (-group["total"], group["category"]))
    return stream.getvalue(), {
        "accepted": sum(g["count"] for g in groups),
        "filtered": filtered,
        "rejected": len(errors),
        "groups": groups,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("-o", "--output")
    args = parser.parse_args()
    if not 1 <= args.rows <= 100_000:
        parser.error("rows must be between 1 and 100000")
    data, expected = fixture(args.rows)
    workspace = Workspace(Path(__file__).resolve().parents[1] / "examples/ledger")
    started = time.perf_counter()
    _, compiled = workspace.compile()
    compile_seconds = time.perf_counter() - started
    with tempfile.TemporaryDirectory(prefix="pixel-ledger-benchmark-") as directory:
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
        tracemalloc.start()
        vm = VM(
            compiled.bytecode,
            max_steps=20_000_000,
            input_text=config,
            file_access=FileAccess(root, root),
        )
        started = time.perf_counter()
        output = vm.run()
        seconds = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        actual = json.loads((root / "report.json").read_text())
        assert actual == expected, "Written report differs from independent oracle"
        assert output == [expected], "Printed report differs from independent oracle"
        report = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "rows": args.rows,
            "input_bytes": len(data.encode()),
            "correct": True,
            "compile_seconds": compile_seconds,
            "steps": vm.steps,
            "run_seconds_with_tracemalloc": seconds,
            "python_peak_bytes": peak,
            "live_heap_objects": len(vm.heap),
            "live_heap_items": vm.heap_items,
            "gc_collections": vm.collections,
            "accepted": expected["accepted"],
            "rejected": expected["rejected"],
            "filtered": expected["filtered"],
        }
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n")


if __name__ == "__main__":
    main()
