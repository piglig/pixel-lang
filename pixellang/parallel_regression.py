"""Isolated subprocess test shards with bounded lifetime and complete evidence."""
from collections import Counter
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time

from .regression import fingerprint


def test_ids(suite):
    for test in suite:
        if hasattr(test, '__iter__'):
            yield from test_ids(test)
        else:
            yield test.id()


def timing_costs(directory):
    directory = Path(directory)
    paths = sorted(directory.glob('worker-*/events.jsonl'))
    if not paths:
        paths = [directory / 'events.jsonl']
    costs, started = {}, set()
    for path in paths:
        for line in path.read_text().splitlines():
            event = json.loads(line)
            name = event.get('test')
            if not isinstance(name, str):
                continue
            if event['kind'] == 'start':
                started.add(name)
            if event['kind'] == 'stop':
                duration = event.get('seconds')
                if isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0:
                    costs[name] = max(costs.get(name, 0), duration, .001)
    # An interrupted test is expensive until measured otherwise. History only
    # schedules work; it never skips a test or supplies a passing result.
    fallback = max(costs.values(), default=1)
    for name in started - costs.keys():
        costs[name] = fallback
    return costs


def shards(ids, workers, costs=None):
    if not 1 <= workers <= 16:
        raise ValueError('Workers must be between 1 and 16')
    count = min(workers, len(ids))
    if not costs:
        return [ids[i::workers] for i in range(count)]
    groups, loads = [[] for _ in range(count)], [0.0] * count
    for name in sorted(ids, key=lambda name: -costs.get(name, 1)):
        worker = min(range(count), key=lambda index: loads[index])
        groups[worker].append(name)
        loads[worker] += costs.get(name, 1)
    order = {name: index for index, name in enumerate(ids)}
    return [sorted(group, key=order.get) for group in groups]


def run_parallel(groups, output, root, command, *, timeout=300, selection=None):
    if not math.isfinite(timeout) or timeout <= 0 or not groups or any(not group for group in groups):
        raise ValueError('Nonempty shards and a positive timeout are required')
    output, root = Path(output).resolve(), Path(root).resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = fingerprint(root)
    started = time.monotonic()
    (output / 'manifest.json').write_text(json.dumps(dict(
        files=before, selection=selection, started=time.time(), shards=groups,
        timeout=timeout), indent=2))
    processes, logs = [], []
    interrupted = None
    try:
        for index, group in enumerate(groups):
            destination = output / f'worker-{index}'
            log = (output / f'worker-{index}.log').open('w')
            logs.append(log)
            processes.append(subprocess.Popen(command(group, destination), cwd=root,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
        while any(process.poll() is None for process in processes):
            if time.monotonic() - started > timeout:
                raise TimeoutError(f'Parallel regression exceeded {timeout} seconds')
            time.sleep(0.05)
    except BaseException as error:
        interrupted = error
    finally:
        for process in processes:
            if process.poll() is None:
                # A worker owns this process group; never signal unrelated jobs.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        for log in logs:
            log.close()
    totals = dict(tests=0, failures=0, errors=0, skipped=0,
                  expectedFailures=0, unexpectedSuccesses=0)
    worker_results, all_events = [], []
    valid = interrupted is None
    for index, group in enumerate(groups):
        directory = output / f'worker-{index}'
        exit_code = processes[index].returncode if index < len(processes) else None
        try:
            summary = json.loads((directory / 'summary.json').read_text())
            events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
            observed = Counter(event['test'] for event in events if event['kind'] == 'start')
            complete = observed == Counter(group)
            valid = valid and exit_code == 0 and summary['verified'] and complete
            for key in totals:
                totals[key] += summary[key]
            all_events.extend(dict(event, worker=index) for event in events)
            worker_results.append(dict(worker=index, exitCode=exit_code, complete=complete, **summary))
        except (OSError, ValueError, KeyError, TypeError) as error:
            valid = False
            worker_results.append(dict(worker=index, exitCode=exit_code, complete=False, error=str(error)))
    after = fingerprint(root)
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    summary = dict(totals, workers=worker_results, seconds=time.monotonic() - started,
        sourcesUnchanged=not changed, changedFiles=changed,
        verified=bool(valid and not changed and totals['tests'] == sum(map(len, groups))),
        interrupted=repr(interrupted) if interrupted else None, finished=time.time())
    summary['testsPassed'] = summary['verified']
    peaks = [worker.get('peakRssBytes') for worker in worker_results]
    summary['sumWorkerPeakRssBytes'] = sum(peaks) if all(peak is not None for peak in peaks) else None
    (output / 'events.jsonl').write_text(''.join(json.dumps(event) + '\n'
        for event in sorted(all_events, key=lambda event: event['time'])))
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    if interrupted is not None:
        raise interrupted
    return summary
