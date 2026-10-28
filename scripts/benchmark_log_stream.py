"""Generate a bounded-memory JSONL workload and verify the PixelLang log CLI.

Evidence stays outside the checkout. Run after building the current compiler.
The normal CLI is used, including argument parsing, directory traversal and file
capabilities. Timing includes compilation; RSS is the OS peak child-process value,
not the sum of simultaneous processes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixellang.regression import fingerprint


def power():
    if sys.platform != 'darwin':
        return {'condition': 'not measured'}
    return {name: subprocess.run(['pmset', '-g', name], capture_output=True,
                                 text=True, check=True).stdout for name in ('batt', 'custom')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--megabytes', type=int, default=100)
    parser.add_argument('--program', type=Path, default=ROOT / 'examples/log-analysis')
    args = parser.parse_args()
    before, power_before = fingerprint(ROOT), power()
    output = args.output.resolve()
    if output.is_relative_to(ROOT) or not 1 <= args.megabytes <= 10000:
        parser.error('Use an external output directory and 1..10000 MiB')
    output.mkdir(parents=True, exist_ok=False)
    inputs, reports = output / 'input', output / 'reports'
    (inputs / 'nested').mkdir(parents=True)
    reports.mkdir()
    count = errors = excluded = total = maximum = written = 0
    services, levels = {}, {}
    records = []
    for i in range(300):
        event = dict(timestamp='2026-09-09T10:00:00Z', level=('INFO', 'WARN', 'ERROR')[i % 3],
                     service='service-' + str(i % 7), duration_ms=i % 100, message='log ' + 'x' * 2048)
        records.append((event, (json.dumps(event) + '\n').encode()))
    digest = hashlib.sha256()
    with (inputs / 'a.jsonl').open('wb') as first, (inputs / 'nested/b.jsonl').open('wb') as second:
        row = 0
        while written < args.megabytes * 1024 * 1024:
            event, raw = records[row % len(records)]
            (first if row % 2 == 0 else second).write(raw)
            digest.update(raw)
            written += len(raw)
            row += 1
            if event['level'] == 'INFO':
                excluded += 1
                continue
            count += 1
            errors += event['level'] == 'ERROR'
            total += event['duration_ms']
            maximum = max(maximum, event['duration_ms'])
            services[event['service']] = services.get(event['service'], 0) + 1
            levels[event['level']] = levels.get(event['level'], 0) + 1
    expected = dict(count=count, errors=errors, excluded=excluded, duration_total_ms=total,
                    duration_mean_ms=total / count, duration_max_ms=maximum, services=services,
                    levels=levels, minutes={'2026-09-09T10:00:00Z': count}, problems=[])
    command = [sys.executable, '-m', 'pixellang', 'run', str(args.program.resolve()), '--json',
               '--max-steps', '1000000000', '--read-root', str(inputs), '--write-root', str(reports),
               '--arg=--directory', '--arg=.', '--arg=--level', '--arg=WARN']
    start = time.monotonic()
    with (output / 'stdout.json').open('w') as stdout, (output / 'stderr.log').open('w') as stderr:
        process = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=1800)
    elapsed = time.monotonic() - start
    summary = dict(verified=False, command=command, bytes=written, rows=row, seconds=elapsed,
                   inputsUnchanged=fingerprint(ROOT) == before, inputs=before, powerBefore=power_before,
                   powerAfter=power(),
                   exitCode=process.returncode, inputSequenceSha256=digest.hexdigest(),
                   peakChildRssBytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss *
                   (1 if sys.platform == 'darwin' else 1024), platform=platform.platform())
    if process.returncode == 0:
        actual = json.loads((output / 'stdout.json').read_text())
        report = json.loads((reports / 'report.json').read_text())
        summary['verified'] = actual['output'] == [expected] and report == expected and summary['inputsUnchanged']
        summary['steps'] = actual['steps']
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0 if summary['verified'] else 1


if __name__ == '__main__':
    sys.exit(main())
