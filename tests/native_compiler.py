"""Shared, content-addressed self-hosted compiler driver for native-language tests.

A cache hit requires the same compiler sources, seed artifact and Python runtime
sources. This avoids rebuilding an identical driver for each checker/IR test class.
"""
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from pixellang.compiler_client import CompilerClient
from pixellang.compiler_service import Limits
from pixellang.compiler_worker import CompilerWorker
from pixellang.vm import validate_bytecode
from pixellang.model import PixelError

ROOT = Path(__file__).resolve().parents[1]
# CompilerService validates this executable against the supplied bytecode and map.
NATIVE_COMPILER = ROOT / 'pixellang/artifacts/compiler-native'
DRIVER = '''import "project.pxl" as project
import "bindings.pxl" as bindings
import "types.pxl" as types
import "checker.pxl" as checker
import "instances.pxl" as instances
import "closures.pxl" as closures
import "ir.pxl" as ir
import "assembler.pxl" as assembler
import "bytecode_stream.pxl" as stream
import "source_info.pxl" as sourceInfo
import "debug_info.pxl" as debugInfo
import "flow_projection.pxl" as flowProjection
record Request { files: map[string, string], operation: string }
effect fn main() {
 match jsonDecode::[Request](input()) {
  .Ok(request) => { Run(request) },
  .Err(error) => { print(error) }
 }
}
effect fn Run(request: Request) {
 let sources = project.Build(request.files, "main.pxl")
 if request.operation == "interfaces" {
  for source in sources.modules { print(jsonFrom(sourceInfo.Describe(source.syntax, source.path))) }
  return
 }
 let checked = checker.Check(types.Analyze(bindings.Bind(sources)))
 if request.operation == "native-graphs" { print(jsonFrom(flowProjection.Build(checked))); return }
 if request.operation == "check" {
  print(jsonFrom(checked.diagnostics))
  print(jsonFrom(checked.flows))
 } else {
  var planned = closures.Build(instances.Build(checked))
  let output = assembler.Assemble(ir.Build(&planned))
  print(jsonFrom(output))
  if request.operation == "debug" { print(jsonFrom(debugInfo.Build(&planned))) }
  else { stream.Emit(output) }
 }
}
'''
_loaded = {}


def load_native_compiler(*, driver=DRIVER):
    files = {path.name: path.read_text() for path in sorted((ROOT / 'selfhost').glob('*.pxl'))}
    if driver is not None:
        files['main.pxl'] = driver
    return compile_native_sources(files)


def compile_native_sources(files):
    # An installed artifact built from exactly these sources is already a
    # native compiler. Testing it does not require another self-compilation.
    artifact = ROOT / 'pixellang/artifacts/compiler.json'
    manifest = json.loads((artifact.parent / 'compiler-manifest.json').read_text())
    source_digest = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    encoded = artifact.read_bytes()
    if (manifest.get('sourceSha256') == source_digest
            and manifest.get('sha256') == hashlib.sha256(encoded).hexdigest()):
        code = json.loads(encoded)
        validate_bytecode(code)
        return code
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode())
    for path in [ROOT / 'pixellang/artifacts/compiler.json', *sorted((ROOT / 'pixellang').glob('*.py'))]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    key = digest.hexdigest()
    if key in _loaded:
        return _loaded[key]
    user = os.getuid() if hasattr(os, 'getuid') else 'local'
    directory = Path(tempfile.gettempdir()) / f'pixellang-native-tests-{user}'
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (key + '.json')
    def build():
        with CompilerWorker(limits=Limits(seconds=900, steps=3_000_000_000)) as worker, CompilerClient(worker) as client:
            # This worker is discarded immediately. Its per-session IR cache
            # cannot serve a second request; the compiled driver is cached above.
            return client.request('compile', files=files, incremental=False)['result']['bytecode']
    code = _cached_compile(path, key, build)
    _loaded[key] = code
    return code


def _read_cached(path, key):
    try:
        data = json.loads(path.read_text())
        code = data['bytecode']
        encoded = json.dumps(code, sort_keys=True, separators=(',', ':')).encode()
        if data['key'] == key and hashlib.sha256(encoded).hexdigest() == data['sha256']:
            validate_bytecode(code)
            return code
    except (OSError, ValueError, KeyError, TypeError, PixelError):
        pass
    return None


@contextmanager
def _build_lock(path):
    # Kernel-owned locks are released on process exit, including a killed worker.
    # Never unlink the lock file: waiters must keep locking the same inode.
    with path.with_suffix('.lock').open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _cached_compile(path, key, build):
    code = _read_cached(path, key)
    if code is not None:
        return code
    with _build_lock(path):
        # Another test process may have published the exact artifact while this
        # process waited. Only the lock holder may compile or replace the file.
        code = _read_cached(path, key)
        if code is not None:
            return code
        code = build()
        validate_bytecode(code)
        encoded = json.dumps(code, sort_keys=True, separators=(',', ':')).encode()
        data = dict(key=key, sha256=hashlib.sha256(encoded).hexdigest(), bytecode=code)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, separators=(',', ':'))
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return code


@contextmanager
def native_driver(bytecode):
    """Bind one exact native driver for a batch; keep each execution bounded."""
    from contextlib import closing
    # Reuse the same validated build cache as compiler-service tests, but run
    # the driver's complete entry rather than a resident session entry.
    with tempfile.TemporaryDirectory(prefix='pixel-native-driver-') as directory, \
            closing(native_service(bytecode, session=False)) as service:
        def run(request, *, steps=1_000_000_000, output_chars=64_000_000, root=None):
            return service.native.execute(json.dumps(request), root=Path(directory if root is None else root), steps=steps,
                output_chars=output_chars, file_bytes=64_000_000,
                cancelled=lambda: False, deadline=time.monotonic()+120)
        yield run


def execute_native_driver(bytecode, request, *, steps=1_000_000_000,
                          output_chars=64_000_000):
    """Run the exact driver; never substitute artifacts or fall back to the VM."""
    with native_driver(bytecode) as run:
        return run(request, steps=steps, output_chars=output_chars)


def native_service(code=None, *, limits=None, session=True):
    """Create a test lane with a matching, content-addressed native executable."""
    from pixellang.bootstrap import normalized
    from pixellang.compiler_service import CompilerService
    from pixellang.native_codegen import build_native, entry_session
    import platform
    import shutil
    import subprocess

    if code is None:
        code = load_native_compiler(driver=None)
    data = code.data if hasattr(code, 'data') else code
    # The installed bundle is usable only when every binding check passes.
    try:
        installed = CompilerService(code, limits=limits, native_executable=NATIVE_COMPILER)
        if installed.resident == session:
            return installed
        installed.close()
    except (OSError, ValueError, KeyError):
        pass
    compiler = shutil.which('clang') or shutil.which('cc')
    if compiler is None:
        raise RuntimeError('A C11 compiler is required for native compiler tests')
    digest = hashlib.sha256(normalized(data).encode('utf-8', errors='surrogatepass'))
    digest.update(repr((platform.platform(), platform.machine(), compiler, 1, 1024, session)).encode())
    digest.update(subprocess.run([compiler, '--version'], check=True, capture_output=True).stdout)
    for path in sorted((ROOT / 'pixellang').glob('*.py')) + sorted((ROOT / 'pixellang').glob('native_*.h')):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    user = os.getuid() if hasattr(os, 'getuid') else 'local'
    directory = Path(tempfile.gettempdir()) / f'pixellang-native-tests-{user}' / digest.hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / 'compiler'
    # Keep validation and publication under the same process lock. Failed or
    # interrupted builds cannot publish a service with a partial source map.
    with _build_lock(executable):
        try:
            return CompilerService(code, limits=limits, native_executable=executable)
        except (OSError, ValueError, KeyError):
            build_native(data, executable, cc=compiler, optimization=1,
                         max_depth=1024, session=entry_session(data) if session else None)
        return CompilerService(code, limits=limits, native_executable=executable)
