"""Test native Studio with the current self-hosted compiler in an isolated extension.

Does not replace the distributed compiler or claim fixed-point acceptance.
Run: .venv/bin/python -m tests.run_native_studio
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from tests.native_compiler import load_native_compiler, ROOT
from pixellang.vm import validate_bytecode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', type=Path, help='Reuse an existing native compiler candidate')
    args = parser.parse_args()
    code = json.loads(args.compiler.read_bytes()) if args.compiler else load_native_compiler(driver=None)
    validate_bytecode(code)
    with tempfile.TemporaryDirectory(prefix='pixellang-native-studio-') as directory:
        extension = Path(directory)
        for name in ('src', 'media', 'syntaxes'):
            shutil.copytree(ROOT / 'vscode' / name, extension / name)
        for name in ('package.json', 'language-configuration.json'):
            shutil.copy2(ROOT / 'vscode' / name, extension / name)
        runtime = extension / 'runtime' / 'pixellang'
        shutil.copytree(ROOT / 'pixellang', runtime, ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))
        artifacts = runtime / 'artifacts'
        artifacts.mkdir()
        data = json.dumps(code, separators=(',', ':')).encode()
        (artifacts / 'compiler.json').write_bytes(data)
        (artifacts / 'compiler-manifest.json').write_text(json.dumps(dict(
            protocolVersion=1, sha256=hashlib.sha256(data).hexdigest(),
            provenance='Isolated native Studio test compiler; not a release artifact')))
        subprocess.run(['node', 'scripts/test.js'], cwd=ROOT / 'vscode', check=True,
                       env={**os.environ, 'PIXELLANG_EXTENSION_PATH': str(extension),
                            'PIXELLANG_TEST_SUITE': os.environ.get('PIXELLANG_TEST_SUITE', 'native-graphs')})


if __name__ == '__main__':
    main()
