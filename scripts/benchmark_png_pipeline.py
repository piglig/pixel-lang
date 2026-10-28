"""Measure cold PNG export, compilation and source restoration in one service."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pixellang.bootstrap import compile_with, normalized
from pixellang.compiler_service import CompilerService, Limits
from pixellang.regression import fingerprint, process_usage
from pixellang.vm import VM
from scripts.benchmark_support import power


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compiler', type=Path,
                        help='Compiler artifact to verify without replacing the bundled compiler')
    parser.add_argument('--native-compiler', type=Path,
                        help='Native executable matching --compiler (both must be supplied)')
    workload = parser.add_mutually_exclusive_group()
    workload.add_argument('--functions', type=int,
                        help='Use a small synthetic program instead of the whole compiler')
    workload.add_argument('--workload', type=Path,
                          help='Frozen compiler source-map JSON shared by both comparison runs')
    parser.add_argument('--timeout', type=float, default=1800)
    args = parser.parse_args()
    if (args.compiler is None) != (args.native_compiler is None):
        parser.error('--compiler and --native-compiler must be supplied together')
    limits = Limits(seconds=args.timeout, steps=3_000_000_000)
    if args.functions is not None and args.functions < 1:
        parser.error('functions must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    before = fingerprint(ROOT)
    compiler_path = (args.compiler or ROOT / 'pixellang/artifacts/compiler.json').resolve()
    artifact = compiler_path.read_bytes()
    native_path = (args.native_compiler or ROOT / 'pixellang/artifacts/compiler-native').resolve()
    mapping_path = native_path.with_name(native_path.name + '.map.json')
    native_digest = hashlib.sha256(native_path.read_bytes()).hexdigest()
    mapping_digest = hashlib.sha256(mapping_path.read_bytes()).hexdigest()
    files = {p.name: p.read_text() for p in sorted((ROOT / 'selfhost').glob('*.pxl'))}
    workload_bytes = args.workload.read_bytes() if args.workload else None
    if workload_bytes is not None:
        files = json.loads(workload_bytes)
        if not isinstance(files, dict) or 'main.pxl' not in files or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
            raise ValueError('workload must map source paths to text and include main.pxl')
    if args.functions is not None:
        files = {'main.pxl': '\n'.join(
            f'fn f{i}(n:int)->int=n+{i}' for i in range(args.functions))
            + '\neffect fn main(){print(f0(42))}'}
    summary = dict(verified=False, measurements=[], powerBefore=power(),
                   workload='compiler' if args.functions is None else 'synthetic',
                   execution='native', nativeCompiler=str(native_path),
                   nativeCompilerSha256=native_digest, sourceMapSha256=mapping_digest,
                   compiler=str(compiler_path), compilerSha256=hashlib.sha256(artifact).hexdigest(),
                   workloadSha256=hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                   scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   cachePolicy='incremental=false, reuseArtifacts=false',
                   memoryScope='Host Python process lifetime peak RSS only; excludes native compiler child; phase peaks are cumulative')
    (args.output / 'manifest.json').write_text(json.dumps(before, indent=2))
    service = (CompilerService(json.loads(artifact), limits=limits, native_executable=native_path)
               if args.compiler else CompilerService.installed(limits=limits))

    def measure(operation, **inputs):
        clock_started = time.time()
        began = time.perf_counter()
        response = service.request(dict(version=1, id=operation, operation=operation,
            incremental=False, reuseArtifacts=False, **inputs))
        elapsed = time.perf_counter() - began
        clock_elapsed = time.time() - clock_started
        if response['status'] != 'ok':
            raise RuntimeError(str(response))
        result = response['result']
        data = result.get('image')
        if data is None:
            data = json.dumps(result, sort_keys=True, ensure_ascii=False,
                              separators=(',', ':')).encode()
        row = dict(operation=operation, wallSeconds=elapsed,
                   clockElapsedSeconds=clock_elapsed, clockGapSeconds=clock_elapsed - elapsed,
                   bytes=len(data),
                   sha256=hashlib.sha256(data).hexdigest(), **response['metrics'],
                   **process_usage())
        summary['measurements'].append(row)
        (args.output / 'partial.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(row), flush=True)
        return result

    try:
        image = measure('export-png', files=files, entry='main.pxl', scale=4)['image']
        (args.output / 'program.png').write_bytes(image)
        recovered = measure('compile-png', image=image)['bytecode']
        recovered_bytes = normalized(recovered).encode()
        (args.output / 'compiler.json').write_bytes(recovered_bytes)
        summary['recoveredCompilerSha256'] = hashlib.sha256(recovered_bytes).hexdigest()
        restored = measure('recover-png', image=image)
        assert restored['entry'] in restored['files']
        (args.output / 'restored.json').write_text(json.dumps(restored, ensure_ascii=False))
        # Correctness checks are outside the three timed operations.
        if args.functions is None:
            fresh, steps = compile_with(recovered, {'main.pxl': 'effect fn main(){print(42)}'}, timeout=120)
            assert VM(fresh).run() == [42]
            summary['compilerSmokeSteps'] = steps
        else:
            assert VM(recovered).run() == [42]
            rebuilt = service.request(dict(version=1, operation='compile',
                files=restored['files'], entry=restored['entry'], incremental=False, reuseArtifacts=False))
            assert rebuilt['status'] == 'ok', rebuilt
            assert VM(rebuilt['result']['bytecode']).run() == [42]
        summary['verified'] = True
    finally:
        service.close()
        summary['inputsUnchanged'] = fingerprint(ROOT) == before and compiler_path.read_bytes() == artifact and (
            args.workload is None or args.workload.read_bytes() == workload_bytes) and (
            hashlib.sha256(native_path.read_bytes()).hexdigest() == native_digest and
            hashlib.sha256(mapping_path.read_bytes()).hexdigest() == mapping_digest)
        summary['verified'] = summary['verified'] and summary['inputsUnchanged']
        summary['powerAfter'] = power()
        summary.update(process_usage())
        (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    if not summary['verified']:
        raise RuntimeError('Benchmark inputs changed')


if __name__ == '__main__':
    main()
