"""Measure individual compiler stages with a bounded, instrumented PixelLang driver."""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixellang.bootstrap import read_bytecode_stream
from pixellang.bootstrap_image import HOST_STAGES
from pixellang.profiling import ProfileVM, run_profile
from pixellang.project import compile_project
from pixellang.regression import fingerprint

DRIVER = '''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "instances.pxl" as instances
import "closures.pxl" as closures
import "ir.pxl" as ir
import "assembler.pxl" as assembler
import "bytecode_stream.pxl" as stream
fn phase(name: string) { print("__pixel_profile_phase__:" + name) }
fn main() {
    let files = jsonDecode[map[string]](input())
    phase("project/lex/parse"); let parsed = project.Build(files, "main.pxl")
    phase("bindings"); let bound = bindings.Bind(parsed)
    phase("types"); let resolved = types.Analyze(bound)
    phase("checker"); let checked = checker.Check(resolved)
    phase("instances"); let named = instances.Build(checked)
    phase("closures"); let captured = closures.Build(named)
    phase("ir/frames/schemas"); let lowered = ir.Build(captured)
    phase("assemble"); let built = assembler.Assemble(lowered)
    phase("emit"); stream.Emit(built)
}'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--functions', type=int, default=100)
    parser.add_argument('--self', action='store_true', dest='whole_compiler')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    if args.functions < 1 or args.timeout <= 0:
        parser.error('Function count and timeout must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    before = fingerprint(ROOT)
    sources = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
    code = compile_project(dict(sources, **{'main.pxl': DRIVER}))[1].bytecode
    workload = sources if args.whole_compiler else {'main.pxl': '\n'.join(
        f'fn f{i}(n: int) -> int = n + {i}' for i in range(args.functions)) +
        '\nfn main() {' + ''.join(f'print(f{i}(1));' for i in range(args.functions)) + '}'}
    frozen = json.dumps(sources, sort_keys=True).encode()
    (args.output / 'workload.json').write_text(json.dumps(workload, ensure_ascii=False))
    (args.output / 'driver.pxl').write_text(DRIVER)
    (args.output / 'manifest.json').write_text(json.dumps(dict(
        files=before, sourceSha256=hashlib.sha256(frozen).hexdigest(), timeout=args.timeout,
        wholeCompiler=args.whole_compiler, functions=args.functions), indent=2))
    vm = ProfileVM(code, input_text=json.dumps(workload), max_steps=400_000_000,
                   max_depth=1024, max_heap_items=16_000_000, max_heap_objects=1_000_000,
                   max_output_chars=64_000_000)
    with (args.output / 'events.jsonl').open('w') as events:
        def report(event):
            events.write(json.dumps(event) + '\n'); events.flush()
            print(json.dumps(event), flush=True)
        with ExitStack() as stack:
            for target in HOST_STAGES:
                stack.enter_context(patch(target, side_effect=AssertionError('Host compiler used')))
            result = read_bytecode_stream(run_profile(vm, timeout=args.timeout, report=report))
    if result['diagnostics']:
        raise ValueError(result['diagnostics'])
    current = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
    if sources != current or fingerprint(ROOT) != before:
        raise ValueError('Compiler sources or runtime changed during profiling')
    print('Profile completed; emitted bytecode validated.', flush=True)


if __name__ == '__main__':
    main()
