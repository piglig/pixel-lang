"""Profile real PixelLang PNG delivery/recovery, preserving bounded raw evidence."""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import resource
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixellang.bootstrap import compile_with, normalized, read_bytecode_stream
from pixellang.bootstrap_image import HOST_STAGES, PROFILE
from pixellang.fileaccess import FileAccess
from pixellang.profiling import ScopedProfileVM
from pixellang.project import compile_project
from pixellang.regression import fingerprint
from pixellang.vm import VM
from scripts.native_vm_probe import power


def phase_map(code, sources):
    files = {'picture_colors.pxl': 'color-conversion', 'png.pxl': 'png-codec', 'picture.pxl': 'image-paint/header',
        'picture_reader.pxl': 'tile-recovery/header', 'pixel_decode.pxl': 'semantic-decode',
        'pixel_regions.pxl': 'spatial-analysis', 'pixel_project.pxl': 'module-assembly',
        'bindings.pxl': 'bindings', 'types.pxl': 'types', 'checker.pxl': 'checker',
        'instances.pxl': 'instances', 'closures.pxl': 'closures', 'ir.pxl': 'ir',
        'frames.pxl': 'frames', 'schemas.pxl': 'schemas', 'assembler.pxl': 'assemble',
        'bytecode_stream.pxl': 'bytecode-output', 'project.pxl': 'source-project',
        'lexer.pxl': 'source-lexer', 'parser.pxl': 'source-parser'}
    result = {}
    for key, function in code['functions'].items():
        spans = [ins.get('span') or {} for ins in function['instructions']]
        span = next((s for s in spans if s.get('source') in sources), {})
        source = span.get('source', '')
        text = sources.get(source, '')
        offset = (span.get('start') or {}).get('offset', -1)
        declarations = [m for m in re.finditer(r'(?m)^(?:export )?fn (\w+)\s*\(', text) if m.start() <= offset]
        name = declarations[-1].group(1) if declarations else ''
        phase = files.get(source)
        if source.startswith('pixel_ast'):
            phase = 'pixel-emission'
        elif source in ('pixel_statements.pxl', 'pixel_expressions.pxl', 'pixel_types.pxl', 'pixel_reader.pxl'):
            phase = 'pixel-parse'
        elif source.startswith('checker_'):
            phase = 'checker'
        if source in ('picture.pxl', 'picture_reader.pxl') and name in ('Color', 'Xor', 'Palette', 'Colors'):
            phase = 'color-conversion'
        if phase:
            result[key] = phase
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sources', type=Path, default=ROOT / 'selfhost')
    parser.add_argument('--compiler', type=Path, help='Execute this frozen compiler artifact instead of building a host seed')
    parser.add_argument('--functions', type=int, default=100)
    parser.add_argument('--workload', type=Path, help='Frozen source-map JSON, independent of compiler implementation')
    parser.add_argument('--compiler-smoke', action='store_true', help='Require the recovered program to compile and run a fresh source')
    parser.add_argument('--plain', action='store_true', help='Measure without call-boundary profiling overhead')
    parser.add_argument('--self', action='store_true', dest='whole_compiler')
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    if args.functions < 1 or not 0 < args.timeout < float('inf'):
        parser.error('Positive functions and finite positive timeout required')
    args.output.mkdir(parents=True, exist_ok=False)
    before = fingerprint(ROOT)
    sources = {p.name: p.read_text() for p in sorted(args.sources.glob('*.pxl'))}
    workload = sources if args.whole_compiler else {'main.pxl': '\n'.join(
        f'fn f{i}(n: int) -> int = n + {i}' for i in range(args.functions)) + '\nfn main(){ print(f0(42)) }'}
    if args.workload:
        workload = json.loads(args.workload.read_text())
    workload_hash = hashlib.sha256(json.dumps(workload,sort_keys=True).encode()).hexdigest()
    (args.output / 'sources.json').write_text(json.dumps(sources, sort_keys=True))
    (args.output / 'workload.json').write_text(json.dumps(workload, sort_keys=True))
    (args.output / 'manifest.json').write_text(json.dumps(dict(files=before, timeout=args.timeout,
        profile=PROFILE, sourcesDirectory=str(args.sources.resolve()), plain=args.plain,
        workloadSha256=workload_hash, scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()), indent=2))
    started = time.perf_counter()
    compiler_bytes = args.compiler.read_bytes() if args.compiler else None
    seed = json.loads(compiler_bytes) if compiler_bytes else compile_project(sources)[1].bytecode
    phases = phase_map(seed, sources)
    (args.output / 'function-phases.json').write_text(json.dumps(phases, indent=2))
    summary = dict(stage0Seconds=time.perf_counter()-started, stages={}, powerBefore=power(),
        compilerSha256=hashlib.sha256(compiler_bytes).hexdigest() if compiler_bytes else None,
        compilerOrigin='frozen-artifact' if compiler_bytes else 'host-seed',
        profiled=not args.plain)
    requests = [('delivery', dict(source='main.pxl', text='', files=workload, operation='png-file', file='program.png', scale=4)),
        ('recovery', dict(source='program.png', text='', operation='png-file-bytecode-stream',
                          file='program.png', byteLimit=64000000, imageLimit=64000000))]
    try:
        for stage, request in requests:
            setup_started = time.perf_counter()
            vm_class = VM if args.plain else ScopedProfileVM
            extra = {} if args.plain else dict(function_phases=phases)
            vm = vm_class(seed, **PROFILE, **extra, input_text=json.dumps(request),
                file_access=FileAccess(read_root=args.output, write_root=args.output, budget=64000000))
            setup_seconds = time.perf_counter()-setup_started
            began = time.perf_counter()
            with ExitStack() as stack:
                for target in HOST_STAGES:
                    stack.enter_context(patch(target, side_effect=AssertionError('Host frontend/codec used')))
                try:
                    while not vm.halted:
                        vm.step(snapshot=False)
                        if vm.steps % 1000000 == 0:
                            elapsed = time.perf_counter()-began
                            print(json.dumps(dict(stage=stage, seconds=elapsed, steps=vm.steps,
                                heapItems=vm.heap_items, heapObjects=len(vm.heap))), flush=True)
                            if elapsed > args.timeout:
                                raise TimeoutError(f'{stage} exceeded {args.timeout} seconds')
                finally:
                    summary['stages'][stage] = (dict(seconds=time.perf_counter()-began,steps=vm.steps,halted=vm.halted)
                        if args.plain else vm.phase_summary())
                    summary['stages'][stage]['vmSetupSeconds'] = setup_seconds
                    summary['stages'][stage]['wallSeconds'] = time.perf_counter()-began
                    summary['stages'][stage]['heapItems'] = vm.heap_items
                    summary['stages'][stage]['heapObjects'] = len(vm.heap)
                    (args.output/'partial.json').write_text(json.dumps(summary, indent=2))
            if stage == 'delivery':
                assert len(vm.output)==1 and not vm.output[0]['diagnostics']
            else:
                built = read_bytecode_stream(vm.output)
                assert not built['diagnostics']
                artifact = normalized(built['bytecode'])
                (args.output/'recovered.json').write_text(artifact)
                summary['recoveredSha256'] = hashlib.sha256(artifact.encode()).hexdigest()
                if args.whole_compiler or args.compiler_smoke:
                    fresh, steps = compile_with(built['bytecode'], {'main.pxl':'fn main(){print(42)}'},timeout=60)
                    assert VM(fresh).run()==[42]
                    summary['compilerSmokeSteps']=steps
                if not args.whole_compiler and not args.workload:
                    assert VM(built['bytecode']).run()==[42]
        summary['verified'] = True
    finally:
        summary['inputsUnchanged'] = before == fingerprint(ROOT) and sources == {p.name:p.read_text() for p in args.sources.glob('*.pxl')}
        summary['inputsUnchanged'] = summary['inputsUnchanged'] and (not args.compiler or args.compiler.read_bytes()==compiler_bytes)
        summary['inputsUnchanged'] = summary['inputsUnchanged'] and (not args.workload or workload_hash==hashlib.sha256(json.dumps(json.loads(args.workload.read_text()),sort_keys=True).encode()).hexdigest())
        summary['verified'] = summary.get('verified', False) and summary['inputsUnchanged']
        summary['seconds'] = time.perf_counter()-started
        summary['powerAfter'] = power()
        summary['peakRssBytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
        (args.output/'summary.json').write_text(json.dumps(summary, indent=2))
    if not summary['verified']:
        raise RuntimeError('Profile inputs changed')


if __name__ == '__main__':
    main()
