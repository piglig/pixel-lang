"""Measure native value updates without including compilation or VM setup.

Development measurements can diagnose scaling; release comparisons require an idle
machine with the same recorded power condition and runtime/compiler fingerprints.
"""
import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixellang.compiler_service import CompilerService
from pixellang.vm import VM
from pixellang.model import PixelError

SOURCES = {
    'append': '''effect fn main() {
 match parseInt(input()) {
  .Ok(count) => run(count),
  .Err(error) => print(error.code)
 }
}
effect fn run(count: int) {
 var values: [int]=[]
 var index=0
 while index<count { values.append(index); index+=1 }
 print(len(values));print(values[count-1])
}''',
    'indexed-write': '''effect fn main() {
 match parseInt(input()) {
  .Ok(count) => run(count),
  .Err(error) => print(error.code)
 }
}
effect fn run(count: int) {
 let original=arrayRepeat([0],count)
 var values=original
 var index=0
 while index<count { values[index]=index; index+=1 }
 print(original[count-1]);print(values[count-1])
}''',
    'map-insert': '''effect fn main() {
 match parseInt(input()) {
  .Ok(count) => run(count),
  .Err(error) => print(error.code)
 }
}
effect fn run(count: int) {
 var values: map[int,int]=map[int,int]{}
 var index=0
 while index<count { values[index]=index; index+=1 }
 print(len(values));print(values[count-1])
}''',
    'spatial-state': '''frame World = meters
flow Scene() step(value: int) -> Space[World, int, string] {
 state world = Space::[World, int, string]("scene")
 let updated = world.insert(string(value), Point3::[World]{x: 0.0, y: 0.0, z: float64(value)}, value)
 next world = updated
 output updated
}
effect fn main() {
 match parseInt(input()) {
  .Ok(count) => run(count),
  .Err(error) => print(error.code)
 }
}
effect fn run(count: int) {
 let scene = Scene()
 let original = scene.step(0)
 var latest = original
 var index = 0
 while index < count { latest = scene.step(index + 1); index += 1 }
 print(original.size());print(latest.size())
}''',

}


def power():
    if sys.platform != 'darwin':
        return {'available': False}
    return {name: subprocess.check_output(['pmset', '-g', option], text=True)
            for name, option in (('source', 'batt'), ('settings', 'custom'))}


def fingerprint(compiler, native):
    paths = [*sorted((ROOT/'pixellang').glob('*.py')), Path(__file__).resolve(), native, native.with_name(native.name+'.map.json')]
    return {'compilerSha256': hashlib.sha256(compiler.read_bytes()).hexdigest(),
            'files': {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', type=Path, default=ROOT/'pixellang/artifacts/compiler.json')
    parser.add_argument('--native-compiler', type=Path, default=ROOT/'pixellang/artifacts/compiler-native')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sizes', type=int, nargs='+', default=[500, 1000, 2000])
    parser.add_argument('--samples', type=int, default=3)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--power-condition', default='uncontrolled-development')
    args = parser.parse_args()
    if args.output.exists() or (args.profile and args.profile.exists()):
        parser.error('Use new output paths')
    if any(n < 2 or n > 100000 for n in args.sizes) or args.samples < 1:
        parser.error('Sizes must be 2..100000 and samples must be positive')
    before = fingerprint(args.compiler, args.native_compiler)
    report = dict(scope=__doc__, inputs=before, platform=platform.platform(), python=sys.version,
                  powerCondition=args.power_condition, powerBefore=power(), sources=SOURCES,
                  measurements=[], compilation={}, verified=False,
                  heapMetricsScope='VM heap only; excludes native spatial storage and process RSS')
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report, indent=2)+'\n')

    save()
    service = CompilerService(json.loads(args.compiler.read_text()), native_executable=args.native_compiler)
    codes = {}
    for kind, source in SOURCES.items():
        began = time.perf_counter()
        response = service.request(dict(version=1, operation='compile', files={'main.pxl':source}))
        if response['status'] != 'ok':
            raise RuntimeError(response)
        codes[kind] = response['result']['bytecode']
        report['compilation'][kind] = dict(seconds=time.perf_counter()-began,
            sha256=hashlib.sha256(json.dumps(codes[kind],sort_keys=True).encode()).hexdigest())
    profiler = cProfile.Profile() if args.profile else None
    for kind, code in codes.items():
        for size in args.sizes:
            trials = []
            for _ in range(args.samples):
                vm = VM(code,input_text=str(size),max_steps=100_000_000,
                        max_heap_items=max(1_000_000,size*16),max_heap_objects=1_000_000)
                began = time.perf_counter()
                if profiler: profiler.enable()
                budget_error = None
                try:
                    output = vm.run()
                except PixelError as error:
                    if kind != 'spatial-state' or error.fields()['code'] != 'spatial.budget':
                        raise
                    budget_error = error.fields()
                finally:
                    if profiler: profiler.disable()
                elapsed = time.perf_counter()-began
                if budget_error is not None:
                    trials.append(dict(status='budget-exceeded', secondsToLimit=elapsed,
                                       error=budget_error, spatialItems=vm.spatial_items,
                                       heapItemLimit=vm.max_heap_items, steps=vm.steps))
                    continue
                expected = ([1, size + 1] if kind == 'spatial-state'
                            else [0 if kind == 'indexed-write' else size, size - 1])
                if output != expected:
                    raise AssertionError((kind,size,output,expected))
                trials.append(dict(status='completed', seconds=elapsed, spatialItems=vm.spatial_items,
                                   allocatedObjects=vm.next_address,
                                   remainingHeapItems=vm.heap_items))
            statuses = {t['status'] for t in trials}
            if len(statuses) != 1:
                raise AssertionError((kind, size, 'inconsistent completion', statuses))
            completed = statuses == {'completed'}
            item = dict(kind=kind, size=size, trials=trials, completed=completed,
                        medianSeconds=statistics.median(t['seconds'] for t in trials) if completed else None)
            report['measurements'].append(item)
            save()
            print(json.dumps(item),flush=True)
    if profiler:
        args.profile.parent.mkdir(parents=True,exist_ok=True)
        profiler.dump_stats(str(args.profile))
    report['allWorkloadsCompleted'] = all(m['completed'] for m in report['measurements'])
    report['profiled'] = profiler is not None
    report['powerAfter'] = power()
    report['verified'] = fingerprint(args.compiler, args.native_compiler) == before
    save()
    if not report['verified']:
        raise RuntimeError('Benchmark inputs changed')


if __name__ == '__main__':
    main()
