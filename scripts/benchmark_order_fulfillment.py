"""Measure the business example's edit compilation and atomic batch scaling.

Development measurements only: no speedup or controlled power conditions assumed.
Compiler metrics and VM execution are reported separately. Spatial allocation
accounting is not a measurement of process memory or retained live bytes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixellang.compiler_service import CompilerService
from pixellang.vm import VM

WORKLOAD = '''import "model.pxl" as model
import "operations.pxl" as operations
import "workflow.pxl" as workflow
effect fn main() {
 let count = COUNT
 let initial = operations.Open("benchmark",count)
 let orders = workflow.Fulfillment(initial)
 var commands: [model.Command] = []
 var i = 0
 while i < count {
  let key = string(i)
  commands.append(.Create(key,100,1))
  commands.append(.Pay(key,100))
  i += 1
 }
 let view = orders.step(.Submit(commands))
 assert(match view.outcome { .Accepted => true, .Rejected(_) => false })
 print([operations.Available(view.scene),operations.Reserved(view.scene),view.scene.linkCount(),initial.size()])
}'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', nargs='+', type=int, default=[100, 300, 1000])
    parser.add_argument('--trials', type=int, default=3)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.trials < 1 or any(n < 1 for n in args.sizes):
        parser.error('sizes and trials must be positive')
    files = {p.name: p.read_text() for p in (ROOT/'examples/order-fulfillment').glob('*.pxl')}
    report = dict(controlledBenchmark=False, platform=platform.platform(), python=sys.version,
                  sourceSha256=hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                  compilerSha256=hashlib.sha256((ROOT/'pixellang/artifacts/compiler.json').read_bytes()).hexdigest(),
                  compilation=[], batches=[])
    service = CompilerService.installed()
    def compile(files, name):
        start = time.perf_counter()
        response = service.request(dict(version=1, operation='compile', files=files, reuseArtifacts=False))
        if response['status'] != 'ok':
            raise RuntimeError(response)
        report['compilation'].append(dict(name=name, wallSeconds=time.perf_counter()-start,
                                          metrics=response['metrics']))
        return response['result']['bytecode']
    try:
        compile(files, 'cold-project')
        changed = dict(files)
        changed['model.pxl'] = changed['model.pxl'].replace('Payment amount does not match', 'Incorrect payment amount')
        compile(changed, 'one-function-body-edit')
        for count in args.sizes:
            workload = dict(files, **{'main.pxl': WORKLOAD.replace('COUNT', str(count))})
            code = compile(workload, f'batch-{count}')
            runs = []
            for _ in range(args.trials):
                vm = VM(code, max_steps=20_000_000)
                start = time.perf_counter()
                output = vm.run()
                elapsed = time.perf_counter() - start
                if output != [[0, count, count, 1]]:
                    raise AssertionError(output)
                runs.append(dict(seconds=elapsed, steps=vm.steps, spatialAllocationItems=vm.spatial_items))
            report['batches'].append(dict(orders=count, commands=2*count,
                medianSeconds=statistics.median(r['seconds'] for r in runs), trials=runs))
    finally:
        service.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
