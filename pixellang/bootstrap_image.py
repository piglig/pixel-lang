"""Verify a frozen compiler source snapshot through executable PNG delivery.

Starting from a compiled seed, PixelLang generates the compiler's semantic image,
reads that PNG and compiles its pixels; the resulting compiler builds a smoke
program. This is a separate gate from bytecode-generation fixed-point equality.
"""

import argparse
import hashlib
import json
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from .bootstrap import compile_with, normalized, read_bytecode_stream
from .fileaccess import FileAccess
from .vm import VM, validate_bytecode
from .regression import fingerprint

HOST_STAGES = (
    'pixellang.text.parse_text', 'pixellang.parser.parse', 'pixellang.spatial.analyze',
    'pixellang.picture.encode_picture', 'pixellang.picture.decode_picture',
    'PIL.Image.open', 'PIL.Image.Image.save',
)
PROFILE = dict(max_steps=3_000_000_000, max_depth=1024, max_heap_items=64_000_000,
               max_heap_objects=2_000_000, max_output_chars=64_000_000)


def run_vm(vm, report, interval=1_000_000, timeout=None):
    if type(interval) is not int or interval < 1:
        raise ValueError('Progress interval must be positive')
    if timeout is not None and not 0 < timeout < float('inf'):
        raise ValueError('Timeout must be finite and positive')
    started = time.monotonic()
    with ExitStack() as stack:
        for target in HOST_STAGES:
            stack.enter_context(patch(target, side_effect=AssertionError('Host frontend/image codec used')))
        while not vm.halted:
            vm.step(snapshot=False)
            if vm.steps % interval == 0:
                report(dict(steps=vm.steps, heapItems=vm.heap_items, heapObjects=len(vm.heap),
                            function=vm.call_stack[-1].function if vm.call_stack else None))
                if timeout is not None and time.monotonic() - started > timeout:
                    raise TimeoutError(f'VM stage exceeded {timeout} seconds')
    report(dict(steps=vm.steps, heapItems=vm.heap_items, heapObjects=len(vm.heap), halted=True))
    return vm.output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New artifact directory')
    parser.add_argument('--seed', type=Path,
                        help='Compiled PixelLang seed; defaults to the bundled compiler')
    parser.add_argument('--stage-timeout', type=float, default=600)
    parser.add_argument('--heap-items', type=int, default=PROFILE['max_heap_items'])
    args = parser.parse_args()
    if args.heap_items < 1:
        parser.error('Heap item budget must be positive')
    if not 0 < args.stage_timeout < float('inf'):
        parser.error('Stage timeout must be finite and positive')
    profile = dict(PROFILE, max_heap_items=args.heap_items)
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    seed_path = (args.seed or root / 'pixellang/artifacts/compiler.json').resolve()
    seed_bytes = seed_path.read_bytes()
    seed = json.loads(seed_bytes)
    validate_bytecode(seed)
    before = fingerprint(root)
    files = {p.name: p.read_text() for p in sorted((root / 'selfhost').glob('*.pxl'))}
    snapshot = json.dumps(files, sort_keys=True, ensure_ascii=False)
    (args.output / 'sources.json').write_text(snapshot)
    manifest = dict(sourceSha256=hashlib.sha256(snapshot.encode()).hexdigest(), files=len(files),
                    fingerprints=before, profile=profile, stageTimeout=args.stage_timeout,
                    seed=str(seed_path), seedSha256=hashlib.sha256(seed_bytes).hexdigest())
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    stage = 'compiled-seed'
    started = time.monotonic()
    with (args.output / 'events.jsonl').open('w') as events:
        def report(details):
            event = dict(stage=stage, elapsed=time.monotonic() - started, **details)
            text = json.dumps(event, ensure_ascii=False)
            events.write(text + '\n')
            events.flush()
            (args.output / 'status.json').write_text(text)
            print(text, flush=True)
        try:
            report(dict(status='starting'))
            stage = 'compiler-png'
            access = FileAccess(write_root=args.output, budget=64_000_000)
            vm = VM(seed, **profile, file_access=access, input_text=json.dumps(dict(
                source='main.pxl', text='', files=files, operation='png-file', file='compiler.png', scale=4)))
            output = run_vm(vm, report, timeout=args.stage_timeout)
            if len(output) != 1 or output[0]['diagnostics']:
                raise ValueError('PNG delivery failed: ' + json.dumps(output, ensure_ascii=False))
            png = args.output / 'compiler.png'
            if output[0]['bytes'] != png.stat().st_size or not output[0]['bytes']:
                raise ValueError('PNG delivery byte count mismatch')
            report(dict(status='delivered', bytes=png.stat().st_size))
            stage = 'png-compiler'
            vm = VM(seed, **profile, file_access=FileAccess(read_root=args.output, budget=64_000_000),
                    input_text=json.dumps(dict(source='compiler.png', text='', file='compiler.png',
                                               operation='png-file-bytecode-stream', byteLimit=64_000_000,
                                               imageLimit=64_000_000)))
            output = read_bytecode_stream(run_vm(vm, report, timeout=args.stage_timeout))
            if output['diagnostics']:
                raise ValueError(json.dumps(output['diagnostics'], ensure_ascii=False))
            compiler = output['bytecode']
            artifact = normalized(compiler)
            (args.output / 'compiler.json').write_text(artifact)
            stage = 'generated-compiler-smoke'
            report(dict(status='starting'))
            program, steps = compile_with(compiler, {'main.pxl': 'effect fn main() { print(6 * 7) }'})
            if VM(program).run() != [42]:
                raise ValueError('PNG-delivered compiler smoke failed')
            unchanged = fingerprint(root) == before and seed_path.read_bytes() == seed_bytes
            summary = dict(manifest, pngSha256=hashlib.sha256(png.read_bytes()).hexdigest(),
                           compilerSha256=hashlib.sha256(artifact.encode()).hexdigest(),
                           smokeSteps=steps, inputsUnchanged=unchanged, verified=unchanged)
            (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
            if not summary['verified']:
                raise ValueError('Verification inputs changed')
            report(dict(status='verified'))
        except BaseException as error:
            report(dict(status='failed', error=str(error)))
            raise


if __name__ == '__main__':
    main()
