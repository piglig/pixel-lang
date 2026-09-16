"""Verify complete compiler semantic-pixel round trips before PNG packaging."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixellang.bootstrap import compile_with, normalized, read_bytecode_stream
from pixellang.bootstrap_image import run_vm
from pixellang.project import compile_project
from pixellang.regression import fingerprint
from pixellang.vm import VM

DRIVER = '''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "pixel_ast.pxl" as pixels
import "pixel_project.pxl" as native
import "spatial_layout.pxl" as layout
import "instances.pxl" as instances
import "closures.pxl" as closures
import "ir.pxl" as ir
import "assembler.pxl" as assembler
import "bytecode_stream.pxl" as stream
fn main() {
    let files = jsonDecode[map[string]](input())
    let encoded = pixels.Build(checker.Check(types.Analyze(bindings.Bind(project.Build(files, "main.pxl")))))
    if len(encoded.diagnostics) > 0 { fail(jsonStringify(jsonFrom(encoded.diagnostics))) }
    let source = encoded.source
    let root = layout.Source{magic: source.magic, versions: source.versions, encoding: source.encoding, dimensions: source.dimensions, pixels: source.pixels, spatial: source.spatial, metadata: source.metadata}
    let decoded = native.Build(root, source.bundle.modules)
    let checked = checker.Check(types.Analyze(bindings.Bind(decoded)))
    stream.Emit(assembler.Assemble(ir.Build(closures.Build(instances.Build(checked)))))
}'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    before = fingerprint(ROOT)
    script = Path(__file__).read_bytes()
    files = {p.name: p.read_text() for p in sorted((ROOT / 'selfhost').glob('*.pxl'))}
    (args.output / 'sources.json').write_text(json.dumps(files, ensure_ascii=False, sort_keys=True))
    (args.output / 'driver.pxl').write_text(DRIVER)
    profile = dict(max_steps=5_000_000_000, max_depth=1024, max_heap_items=64_000_000,
                   max_heap_objects=2_000_000, max_output_chars=64_000_000)
    (args.output / 'manifest.json').write_text(json.dumps(dict(files=before, profile=profile), indent=2))
    seed = compile_project(dict(files, **{'main.pxl': DRIVER}))[1].bytecode
    started = time.monotonic()
    with (args.output / 'events.jsonl').open('w') as events:
        def report(details):
            event = dict(elapsed=time.monotonic() - started, **details)
            line = json.dumps(event)
            events.write(line + '\n'); events.flush()
            (args.output / 'status.json').write_text(line)
            print(line, flush=True)
        try:
            vm = VM(seed, **profile, input_text=json.dumps(files))
            output = read_bytecode_stream(run_vm(vm, report, interval=10_000_000))
            if output['diagnostics']:
                raise ValueError(json.dumps(output['diagnostics']))
            code = output['bytecode']
            artifact = normalized(code)
            (args.output / 'compiler.json').write_text(artifact)
            program, steps = compile_with(code, {'main.pxl': 'fn main() { print(6 * 7) }'})
            if VM(program).run() != [42]:
                raise ValueError('Pixel compiler smoke failed')
            unchanged = fingerprint(ROOT) == before and Path(__file__).read_bytes() == script
            summary = dict(verified=unchanged, inputsUnchanged=unchanged, smokeSteps=steps,
                           compilerSha256=hashlib.sha256(artifact.encode()).hexdigest())
            (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
            report(summary)
            if not unchanged:
                raise ValueError('Verification inputs changed')
        except BaseException as error:
            report(dict(status='failed', error=str(error)))
            raise


if __name__ == '__main__':
    main()
