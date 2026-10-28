"""Measure individual compiler stages with a bounded, instrumented PixelLang driver."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import math
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixellang.bootstrap import read_bytecode_stream
from pixellang.bootstrap_image import HOST_STAGES
from pixellang.profiling import ScopedProfileVM, run_profile
from pixellang.native_bundle import load_bundle
from pixellang.compiler_service import CompilerService
from pixellang.vm import validate_bytecode
from scripts.profile_image import phase_map
from pixellang.regression import fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, help='Frozen native compiler; defaults to the verified bundled artifact')
    parser.add_argument('--functions', type=int, default=100)
    parser.add_argument('--self', action='store_true', dest='whole_compiler')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    if args.functions < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('Function count and timeout must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    before = fingerprint(ROOT)
    sources = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
    code = json.loads(args.compiler.read_bytes()) if args.compiler else load_bundle(Path(__file__).resolve().parents[1]/'pixellang/artifacts')[0]
    validate_bytecode(code)
    phases = phase_map(code, sources)
    (args.output / 'function-phases.json').write_text(json.dumps(phases, indent=2))
    workload = sources if args.whole_compiler else {'main.pxl': '\n'.join(
        f'fn f{i}(n: int) -> int = n + {i}' for i in range(args.functions)) +
        '\neffect fn main() {' + ''.join(f'print(f{i}(1));' for i in range(args.functions)) + '}'}
    frozen = json.dumps(sources, sort_keys=True).encode()
    (args.output / 'workload.json').write_text(json.dumps(workload, ensure_ascii=False))
    (args.output / 'manifest.json').write_text(json.dumps(dict(
        files=before, compilerSha256=hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest(), sourceSha256=hashlib.sha256(frozen).hexdigest(), timeout=args.timeout,
        wholeCompiler=args.whole_compiler, functions=args.functions), indent=2))
    request = dict(source='main.pxl', text='', operation='bytecode-stream', files=workload)
    vm = ScopedProfileVM(code, function_phases=phases, input_text=json.dumps(request), max_steps=400_000_000,
                   max_depth=1024, max_heap_items=16_000_000, max_heap_objects=1_000_000,
                   max_output_chars=64_000_000)
    with (args.output / 'events.jsonl').open('w') as events:
        def report(event):
            events.write(json.dumps(event) + '\n'); events.flush()
            print(json.dumps(event), flush=True)
        with ExitStack() as stack:
            for target in HOST_STAGES:
                stack.enter_context(patch(target, side_effect=AssertionError('Host compiler used')))
            try:
                result = read_bytecode_stream(run_profile(vm, timeout=args.timeout, report=report))
            finally:
                (args.output / 'phases.json').write_text(json.dumps(vm.phase_summary(), indent=2))
    if result['diagnostics']:
        raise ValueError(result['diagnostics'])
    current = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
    if sources != current or fingerprint(ROOT) != before:
        raise ValueError('Compiler sources or runtime changed during profiling')
    print('Profile completed; emitted bytecode validated.', flush=True)


if __name__ == '__main__':
    main()
