"""Bounded, reproducible compiler and PNG benchmarks (no bootstrap required)."""
import argparse
import json
import time
from pathlib import Path

from pixellang.project import compile_project
from pixellang.fileaccess import FileAccess
from pixellang.vm import VM
from pixellang.bootstrap import read_bytecode_stream

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-dir', type=Path)
    parser.add_argument('--image', type=Path, help='Also round-trip an existing PNG with PixelLang')
    args = parser.parse_args()
    sources = {p.name: p.read_text() for p in (ROOT / 'selfhost').glob('*.pxl')}
    if args.baseline_dir:
        for path in args.baseline_dir.glob('*.before.pxl'):
            sources[path.name.replace('.before', '')] = path.read_text()
    compiler = compile_project(sources)[1].bytecode
    results = []

    def measure(name, code, request, access=None):
        vm = VM(code, input_text=json.dumps(request), max_steps=100_000_000,
                file_access=access,
                max_heap_items=64_000_000 if access else 16_000_000, max_heap_objects=1_000_000,
                max_output_chars=64_000_000, max_depth=1024)
        start = time.perf_counter()
        output = vm.run()
        elapsed = time.perf_counter() - start
        result = dict(name=name, seconds=round(elapsed, 6), steps=vm.steps)
        results.append(result)
        args.output.write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps(result), flush=True)
        return output

    for count in (100, 200):
        source = '\n'.join(f'fn f{i}(n: int) -> int = n + {i}' for i in range(count))
        source += '\nfn main() {' + ''.join(f'print(f{i}(1));' for i in range(count)) + '}'
        output = measure(f'instances-{count}', compiler, dict(
            source='main.pxl', text='', operation='instances', files={'main.pxl': source}))
        assert output[0]['diagnostics'] == [], output
    for operation in ('encode', 'roundtrip'):
        driver = '''import "png.pxl" as png
fn main() {
    let rgba = jsonDecode[[int]](input())
    let encoded = png.Encode(128, 128, rgba)
    BODY
}'''.replace('BODY', 'print(len(encoded))' if operation == 'encode' else
            'let image = png.Decode(encoded, 65536); assert(crc32(image.rgba) == crc32(rgba)); print(len(image.rgba))')
        code = compile_project({'main.pxl': driver, 'png.pxl': sources['png.pxl']})[1].bytecode
        measure('png-' + operation, code, [16, 20, 24, 255] * (128 * 128))
    if not args.baseline_dir:
        assert results[1]['steps'] < 8_000_000, 'Instance planning performance regression'
        assert results[1]['steps'] < results[0]['steps'] * 2.2, 'Superlinear instance planning'
        assert results[2]['steps'] < 20_000, 'PNG encoding performance regression'
        assert results[3]['steps'] < 60_000, 'PNG round-trip performance regression'
    source = '\n'.join(f'fn f{i}(n: int) -> int = n + {i}' for i in range(100))
    source += '\nfn main() {' + ''.join(f'print(f{i}(1));' for i in range(100)) + '}'
    compiled = read_bytecode_stream(measure('compile-100', compiler, dict(
        source='main.pxl', text='', operation='bytecode-stream', files={'main.pxl': source})))
    assert compiled['diagnostics'] == [], compiled['diagnostics']
    assert VM(compiled['bytecode']).run() == list(range(1, 101))
    if not args.baseline_dir:
        assert results[-1]['steps'] < 6_000_000, 'Full compilation performance regression'
    if args.image:
        driver = '''import "png.pxl" as png
fn main() {
    let bytes = readBytes(jsonDecode[string](input()), 64000000)
    let image = png.Decode(bytes, 64000000)
    let encoded = png.Encode(image.width, image.height, image.rgba)
    print(image.width); print(image.height); print(crc32(image.rgba)); print(len(encoded))
}'''
        code = compile_project({'main.pxl': driver, 'png.pxl': sources['png.pxl']})[1].bytecode
        measure('image-roundtrip', code, args.image.name,
                FileAccess(read_root=args.image.resolve().parent, budget=64_000_000))
    args.output.write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
