"""Measure uncached native compilation without rebuilding the compiler or PNGs."""
import argparse
import cProfile
from contextlib import closing
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixellang.compiler_service import CompilerService
from pixellang.vm import VM
from scripts.benchmark_support import power


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path, nargs='?')
    parser.add_argument('--functions', type=int, help='Synthetic reachable function count instead of a project')
    parser.add_argument('--compiler', type=Path)
    parser.add_argument('--native-compiler', type=Path)
    parser.add_argument('--entry', default='main.pxl')
    parser.add_argument('--repeat', type=int, default=5)
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory')
    parser.add_argument('--profile', action='store_true', help='Additional instrumented run; excluded from timings')
    args = parser.parse_args()
    if not 1 <= args.repeat <= 100:
        parser.error('Repeat must be between 1 and 100')
    if (args.project is None) == (args.functions is None):
        parser.error('Specify a project or --functions, but not both')
    if (args.compiler is None) != (args.native_compiler is None):
        parser.error('--compiler and --native-compiler must be supplied together')
    if args.functions is not None and not 1 <= args.functions <= 10000:
        parser.error('--functions must be in 1..10000')
    project = args.project.resolve() if args.project else None
    if project is not None:
        if not project.is_dir():
            parser.error('Project must be a source directory')
        files = {'std/' + p.name: p.read_text() for p in (ROOT / 'pixellang/stdlib').glob('*.pxl')}
        files.update({p.relative_to(project).as_posix(): p.read_text() for p in project.rglob('*.pxl')})
    else:
        files = {args.entry: '\n'.join(
            f'fn f{i}(n: int) -> int = n + {i}' for i in range(args.functions)) +
            '\neffect fn main(){' + ';'.join(f'print(f{i}(1))' for i in range(args.functions)) + '}'}
    if args.entry not in files:
        parser.error('Entry must name a source in the project')
    args.output.mkdir(parents=True, exist_ok=False)
    digest = lambda data: hashlib.sha256(data).hexdigest()
    runtime = {p.name: digest(p.read_bytes()) for p in sorted((ROOT / 'pixellang').glob('*.py'))}
    compiler_path = args.compiler or ROOT / 'pixellang/artifacts/compiler.json'
    native_path = args.native_compiler or ROOT / 'pixellang/artifacts/compiler-native'
    mapping_path = native_path.with_name(native_path.name + '.map.json')
    manifest = {str(p.resolve()): digest(p.read_bytes()) for p in (compiler_path, native_path, mapping_path)}
    power_before = power()
    (args.output / 'sources.json').write_text(json.dumps(files, ensure_ascii=False, sort_keys=True))
    code = json.loads(compiler_path.read_text()) if args.compiler else None
    request = dict(version=1, operation='compile', entry=args.entry, files=files,
                   incremental=False, reuseArtifacts=False)
    rows = []

    def compile_once():
        startup = time.perf_counter()
        service = (CompilerService(code, native_executable=native_path)
                   if args.compiler else CompilerService.installed())
        startup_seconds = time.perf_counter() - startup
        with closing(service):
            start = time.perf_counter()
            response = service.request(request)
            elapsed = time.perf_counter() - start
        if response['status'] != 'ok':
            raise RuntimeError(json.dumps(response, ensure_ascii=False))
        compiled = response['result']['bytecode']
        if args.functions is not None and VM(compiled).run() != list(range(1, args.functions + 1)):
            raise RuntimeError('Synthetic workload produced incorrect results')
        return dict(seconds=elapsed, serviceStartupSeconds=startup_seconds,
                    steps=response['metrics']['steps'],
                    bytecodeSha256=digest(json.dumps(compiled, sort_keys=True).encode()))

    for _ in range(args.repeat):
        row = compile_once()
        rows.append(row)
        print(json.dumps(row), flush=True)
    if len({(r['steps'], r['bytecodeSha256']) for r in rows}) != 1:
        raise RuntimeError('Repeated compilation changed bytecode or instruction count')
    if args.profile:
        profile = cProfile.Profile()
        profile.enable()
        try:
            profiled = compile_once()
        finally:
            profile.disable()
            profile.dump_stats(str(args.output / 'runtime.prof'))
        if (profiled['steps'], profiled['bytecodeSha256']) != (rows[0]['steps'], rows[0]['bytecodeSha256']):
            raise RuntimeError('Instrumented compilation changed output')
    if runtime != {p.name: digest(p.read_bytes()) for p in sorted((ROOT / 'pixellang').glob('*.py'))}:
        raise RuntimeError('Runtime changed during measurement')
    if manifest != {str(p.resolve()): digest(p.read_bytes()) for p in (compiler_path, native_path, mapping_path)}:
        raise RuntimeError('Compiler artifacts changed during measurement')
    result = dict(project=str(project) if project else None, functions=args.functions,
                  powerBefore=power_before, powerAfter=power(), entry=args.entry, compiler=manifest, runtime=runtime,
                  sourceSha256=digest(json.dumps(files, sort_keys=True).encode()), rows=rows,
                  serviceStartupSeconds=statistics.median(r['serviceStartupSeconds'] for r in rows),
                  medianSeconds=statistics.median(r['seconds'] for r in rows),
                  scope='Independent uncached requests, including native process/protocol startup; excludes host service construction, PNG and program execution; power conditions uncontrolled')
    (args.output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(medianSeconds=result['medianSeconds'], output=str(args.output))), flush=True)


if __name__ == '__main__':
    main()
