"""Recover an existing compiler PNG using an existing PixelLang compiler.

No Python compiler frontend or image codec is used. This allows a delivered image
to be verified again after a runtime fix without regenerating its pixels.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pixellang.bootstrap import compile_with, normalized, read_bytecode_stream
from pixellang.bootstrap_image import PROFILE, run_vm
from pixellang.fileaccess import FileAccess
from pixellang.regression import fingerprint
from pixellang.vm import VM, validate_bytecode


def sha(data):
    return hashlib.sha256(data).hexdigest()


def runtime_fingerprint():
    # This gate consumes the saved artifacts and a literal smoke program. Test
    # helpers and example fixtures are inputs of the separate regression gate.
    return {path: value for path, value in fingerprint(ROOT).items()
            if not path.startswith(('tests/', 'examples/'))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-steps', type=int, default=5_000_000_000)
    args = parser.parse_args()
    if args.max_steps < 1:
        parser.error('max-steps must be positive')
    image = args.image.resolve()
    compiler_bytes = args.compiler.read_bytes()
    image_bytes = image.read_bytes()
    source_bytes = args.sources.read_bytes()
    script_bytes = Path(__file__).read_bytes()
    compiler = json.loads(compiler_bytes)
    validate_bytecode(compiler)
    sources = json.loads(source_bytes)
    current = {p.name: p.read_text() for p in sorted((ROOT / 'selfhost').glob('*.pxl'))}
    before = runtime_fingerprint()
    profile = dict(PROFILE, max_steps=args.max_steps)
    manifest = dict(compilerSha256=sha(compiler_bytes), pngSha256=sha(image_bytes),
                    sourcesSha256=sha(source_bytes), runnerSha256=sha(script_bytes),
                    files=before, profile=profile, sourceSnapshotMatchesCurrent=sources == current)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    started = time.monotonic()
    stage = 'png-compiler'
    with (args.output / 'events.jsonl').open('w') as events:
        def report(details):
            event = dict(stage=stage, elapsed=time.monotonic() - started, **details)
            encoded = json.dumps(event)
            events.write(encoded + '\n')
            events.flush()
            (args.output / 'status.json').write_text(encoded)
            print(encoded, flush=True)
        try:
            vm = VM(compiler, **profile,
                    file_access=FileAccess(read_root=image.parent, budget=64_000_000),
                    input_text=json.dumps(dict(source=image.name, text='', file=image.name,
                        operation='png-file-bytecode-stream', byteLimit=64_000_000,
                        imageLimit=64_000_000)))
            decoded = read_bytecode_stream(run_vm(vm, report, interval=10_000_000))
            if decoded['diagnostics']:
                raise ValueError(json.dumps(decoded['diagnostics'], ensure_ascii=False))
            recovered = decoded['bytecode']
            artifact = normalized(recovered)
            (args.output / 'compiler.json').write_text(artifact)
            stage = 'generated-compiler-smoke'
            report(dict(status='starting'))
            program, steps = compile_with(recovered, {'main.pxl': 'fn main() { print(6 * 7) }'})
            if VM(program).run() != [42]:
                raise ValueError('Recovered compiler smoke failed')
            unchanged = (runtime_fingerprint() == before and image.read_bytes() == image_bytes
                and args.compiler.read_bytes() == compiler_bytes
                and args.sources.read_bytes() == source_bytes
                and Path(__file__).read_bytes() == script_bytes)
            summary = dict(compilerSha256=sha(artifact.encode()), smokeSteps=steps,
                           inputsUnchanged=unchanged, sourceSnapshotMatchesCurrent=sources == current,
                           verified=unchanged and sources == current)
            (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
            report(summary)
            if not summary['verified']:
                raise ValueError('Verification inputs changed or source snapshot is historical')
        except BaseException as error:
            report(dict(status='failed', error=str(error)))
            raise


if __name__ == '__main__':
    main()
