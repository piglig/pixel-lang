"""Verify native spatial programs using an isolated wheel installation and CLI.

The source checkout is used only to copy example inputs. All execution, PNG
emission, recovery and editing checks run outside it against the installed wheel.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', required=True, type=Path)
    parser.add_argument('--compiler', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists():
        parser.error('Report already exists')
    wheel = args.wheel.resolve()
    compiler = args.compiler.resolve()
    expected_hash = hashlib.sha256(compiler.read_bytes()).hexdigest()
    root = Path(tempfile.mkdtemp(prefix='pixel-native-installed-')).resolve()
    env = dict(os.environ)
    for name in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PIXELLANG_DISABLE_CACHE'):
        env.pop(name, None)
    env['PIXELLANG_CACHE_DIR'] = str(root / 'cache')
    commands = []
    report = dict(verified=False, environment=str(root), commands=commands,
                  wheelSha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
                  compilerSha256=expected_hash)
    examples = (
        ('native-space', [1, 0, 2.0, 3.0, True]),
        ('weighted-routes', [[0, 5, 20, -1], [0, 5, 7, 21], [0, 5, 7, 8], [0, 5, 20, 1], False]),
        ('native-elevator', [
            [0, 3, 0], False, [0], 'rejected: Cannot open doors during a trip',
            [1, 3, 0], False, [1], [2, 3, 0], False, [2],
            [3, 3, 1], True, [3], [3, 1, 0], False, [3],
            [2, 1, 0], False, [2], [1, 1, 1], True, [1],
            [0, 0, 1], True, [0], 9.0, 1,
        ]),
    )
    inputs = [Path(__file__), *(path for name, _ in examples
              for path in sorted((ROOT / 'examples' / name).glob('*.pxl')))]
    report['inputs'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}

    def run(*command):
        began = time.monotonic()
        result = subprocess.run(list(map(str, command)), cwd=root, env=env,
                                capture_output=True, text=True, timeout=180)
        commands.append(dict(command=list(map(str, command)), exitCode=result.returncode,
                             seconds=time.monotonic() - began, stderr=result.stderr))
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout

    try:
        run('uv', 'venv', root / 'env')
        python = root / 'env/bin/python'
        run('uv', 'pip', 'install', '--python', python, wheel)
        installed = json.loads(run(python, '-c', '''import hashlib,json,pathlib,pixellang
p=pathlib.Path(pixellang.__file__).parent
print(json.dumps(dict(package=str(p), compilerSha256=hashlib.sha256((p/'artifacts/compiler.json').read_bytes()).hexdigest(), manifest=json.loads((p/'artifacts/compiler-manifest.json').read_text()))))'''))
        if not Path(installed['package']).is_relative_to(root):
            raise AssertionError('Runtime imported from outside the clean environment')
        assert installed['compilerSha256'] == expected_hash
        assert installed['manifest']['sha256'] == expected_hash
        report['installed'] = installed
        pixel = root / 'env/bin/pixel'
        results = []
        report['programs'] = results
        for name, expected in examples:
            project = root / name
            project.mkdir()
            for path in sorted((ROOT / 'examples' / name).glob('*.pxl')):
                shutil.copy2(path, project / path.name)
            (project / 'pixel.toml').write_text(f'format = 1\n[project]\nname = "{name}"\n')
            original = json.loads(run(pixel, 'run', project, '--json'))
            assert original['output'] == expected, original
            build = json.loads(run(pixel, 'build', project, '--json'))
            image = root / (name + '.png')
            shutil.copy2(build['image'], image)
            shutil.rmtree(project)
            delivered = json.loads(run(pixel, 'run', image, '--json'))
            assert delivered['output'] == expected, delivered
            restored = root / (name + '-restored')
            recovered = json.loads(run(pixel, 'unpack', image, '-o', restored, '--json'))
            entry = restored / recovered['entry']
            rebuilt = json.loads(run(pixel, 'run', entry, '--json'))
            assert rebuilt['output'] == expected, rebuilt
            results.append(dict(name=name, output=expected, sourceDeleted=True,
                                pngSha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                                restoredEntry=str(entry)))
            if name == 'weighted-routes':
                source = entry.read_text()
                # Token spacing may change during recovery; change the typed
                # link weight through the recovered source's literal token.
                import re
                changed, count = re.subn(r'(cost\s*:\s*)5\b', r'\g<1>9', source)
                assert count == 1, 'Recovered source must contain exactly one cost=5'
                entry.write_text(changed)
                edited = json.loads(run(pixel, 'run', entry, '--json'))['output']
                assert edited == [[0, 9, 20, -1], [0, 9, 11, 21], [0, 9, 11, 12], [0, 9, 20, 1], False], edited
                results[-1]['editedOutput'] = edited
        assert hashlib.sha256(wheel.read_bytes()).hexdigest() == report['wheelSha256']
        assert hashlib.sha256(compiler.read_bytes()).hexdigest() == expected_hash
        assert {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs} == report['inputs']
        report['verified'] = True
    except BaseException as error:
        report['error'] = str(error)
        raise
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(verified=True, report=str(args.report), environment=str(root))))


if __name__ == '__main__':
    main()
