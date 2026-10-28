"""Optional, bounded project bytecode cache in the user's cache directory.

Only compilation is cached. Dependency locks are checked by Workspace before
entry; runtime inputs, capabilities and effects are always evaluated anew.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from .model import PixelError
from .vm import validate_bytecode

MAX_ENTRY = 32_000_000
MAX_TOTAL = 128_000_000


def directory():
    if os.environ.get('PIXELLANG_DISABLE_CACHE') == '1':
        return None
    override = os.environ.get('PIXELLANG_CACHE_DIR')
    if override:
        return Path(override).expanduser() / 'bytecode-v1'
    base = (Path.home() / 'Library/Caches' if sys.platform == 'darwin' else
            Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')))
    return base / 'pixellang/bytecode-v1'


def identity(files, entry, entry_function=None):
    root = Path(__file__).parent
    artifact = (root / 'artifacts/compiler.json').read_bytes()
    compiler_sha = hashlib.sha256(artifact).hexdigest()
    manifest = json.loads((root / 'artifacts/compiler-manifest.json').read_text())
    if manifest.get('protocolVersion') != 1 or manifest.get('sha256') != compiler_sha:
        raise PixelError('service', 'Installed compiler artifact does not match its manifest')
    digest = hashlib.sha256()
    for value in (sys.version, sys.platform, entry, entry_function or "main", compiler_sha,
                  json.dumps(files, sort_keys=True, ensure_ascii=False)):
        digest.update(value.encode('utf-8')); digest.update(b'\0')
    for path in sorted([*root.glob('*.py'), *(root / 'stdlib').glob('*.pxl')]):
        digest.update(str(path.relative_to(root)).encode()); digest.update(b'\0')
        digest.update(path.read_bytes()); digest.update(b'\0')
    return digest.hexdigest(), compiler_sha


def load(folder, key):
    path = folder / (key + '.json')
    try:
        if path.stat().st_size > MAX_ENTRY:
            return None
        document = json.loads(path.read_bytes())
        if not isinstance(document, dict) or document.get('key') != key:
            return None
        code = document['bytecode']
        raw = json.dumps(code, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        if hashlib.sha256(raw).hexdigest() != document['sha256']:
            return None
        validate_bytecode(code)
        path.touch()
        return code
    except (OSError, ValueError, KeyError, TypeError, PixelError, RecursionError):
        return None


def publish(folder, key, code):
    temporary = None
    try:
        raw = json.dumps(code, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        payload = json.dumps({'key': key, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytecode': code},
                             sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        if len(payload) > MAX_ENTRY:
            return
        folder.mkdir(parents=True, mode=0o700, exist_ok=True)
        with NamedTemporaryFile(dir=folder, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        target = folder / (key + '.json')
        temporary.replace(target)
        entries = sorted(folder.glob('*.json'), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        retained = 0
        for index, path in enumerate(entries):
            retained += path.stat().st_size
            if index >= 32 or retained > MAX_TOTAL:
                path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, RecursionError):
        pass  # A cache failure cannot fail a successful compilation.
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def compile_bytecode(compiler, files, entry, *, entry_function=None):
    if entry_function is not None and (not isinstance(entry_function, str) or not entry_function):
        raise PixelError('project', 'Test entry function must be a nonempty name')
    folder = directory()
    key = compiler_sha = None
    if folder is not None:
        try:
            key, compiler_sha = identity(files, entry, entry_function)
            cached = load(folder, key)
            if cached is not None:
                return cached
        except (OSError, ValueError, UnicodeError):
            key = None
    args = {} if entry_function is None else {'entryFunction': entry_function}
    response = compiler.request('compile', files=files, entry=entry, **args)
    code = response['result']['bytecode']
    if key is not None and response.get('compilerSha256') == compiler_sha:
        try:
            if identity(files, entry, entry_function) == (key, compiler_sha):
                publish(folder, key, code)
        except (OSError, ValueError, UnicodeError, PixelError):
            pass
    return code
