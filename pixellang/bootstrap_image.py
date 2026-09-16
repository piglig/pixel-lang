"""Verify a frozen compiler source snapshot through executable PNG delivery.

Python constructs stage0 once. PixelLang generates the compiler's semantic image,
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
from .vm import VM
from .regression import fingerprint

HOST_STAGES = (
    'pixellang.text.parse_text', 'pixellang.parser.parse', 'pixellang.spatial.analyze',
    'pixellang.semantics.Checker.check', 'pixellang.backend.lower', 'pixellang.backend.assemble',
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
    parser.add_argument('--stage-timeout', type=float, default=600)
    args = parser.parse_args()
    if not 0 < args.stage_timeout < float('inf'):
        parser.error('Stage timeout must be finite and positive')
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    before = fingerprint(root)
    files = {p.name: p.read_text() for p in sorted((root / 'selfhost').glob('*.pxl'))}
    snapshot = json.dumps(files, sort_keys=True, ensure_ascii=False)
    (args.output / 'sources.json').write_text(snapshot)
    manifest = dict(sourceSha256=hashlib.sha256(snapshot.encode()).hexdigest(), files=len(files),
                    fingerprints=before, profile=PROFILE, stageTimeout=args.stage_timeout)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    stage = 'stage0'
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
            from .project import compile_project
            seed = compile_project(files)[1].bytecode
            stage = 'compiler-png'
            access = FileAccess(write_root=args.output, budget=64_000_000)
            vm = VM(seed, **PROFILE, file_access=access, input_text=json.dumps(dict(
                source='main.pxl', text='', files=files, operation='png-file', file='compiler.png', scale=4)))
            output = run_vm(vm, report, timeout=args.stage_timeout)
            if len(output) != 1 or output[0]['diagnostics']:
                raise ValueError('PNG delivery failed: ' + json.dumps(output, ensure_ascii=False))
            png = args.output / 'compiler.png'
            if output[0]['bytes'] != png.stat().st_size or not output[0]['bytes']:
                raise ValueError('PNG delivery byte count mismatch')
            report(dict(status='delivered', bytes=png.stat().st_size))
            stage = 'png-compiler'
            vm = VM(seed, **PROFILE, file_access=FileAccess(read_root=args.output, budget=64_000_000),
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
            program, steps = compile_with(compiler, {'main.pxl': 'fn main() { print(6 * 7) }'})
            if VM(program).run() != [42]:
                raise ValueError('PNG-delivered compiler smoke failed')
            summary = dict(manifest, pngSha256=hashlib.sha256(png.read_bytes()).hexdigest(),
                           compilerSha256=hashlib.sha256(artifact.encode()).hexdigest(),
                           smokeSteps=steps, inputsUnchanged=fingerprint(root) == before,
                           verified=fingerprint(root) == before)
            (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
            if not summary['verified']:
                raise ValueError('Verification inputs changed')
            report(dict(status='verified'))
        except BaseException as error:
            report(dict(status='failed', error=str(error)))
            raise


if __name__ == '__main__':
    main()
