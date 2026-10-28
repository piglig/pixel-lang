"""Persistent, supervised compiler process; cancellation can terminate primitives."""
from dataclasses import asdict
import base64
import json
import io
import pickle
import multiprocessing
import os
import signal
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time

from .compiler_service import CompilerService, Limits, PROTOCOL_VERSION

MAX_WIRE_BYTES = 96_000_000
CANCEL_GRACE_SECONDS = .1


class _ResponseReader(pickle.Unpickler):
    # Private child-to-parent data only. Never resolve Python classes/functions
    # or persistent objects, including for malformed worker output.
    def find_class(self, module, name):
        raise ValueError('Compiler response cannot load Python globals')

    def persistent_load(self, pid):
        raise ValueError('Compiler response cannot load persistent objects')


def dump_response(value):
    """Internal IPC preserves bytes and shared spans without JSON expansion."""
    return pickle.dumps(value,protocol=5)


def load_response(data):
    stream=io.BytesIO(data)
    try:
        value=_ResponseReader(stream).load()
    except (pickle.UnpicklingError,EOFError) as error:
        raise ValueError('Malformed compiler response') from error
    if stream.read(1) or not isinstance(value,dict):
        raise ValueError('Invalid compiler response envelope')
    return value


def encode_wire(value):
    if isinstance(value, bytes):
        return {'$bytes':base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        if '$bytes' in value or '$escaped' in value:
            return {'$escaped':[[k,encode_wire(v)] for k,v in value.items()]}
        return {k:encode_wire(v) for k,v in value.items()}
    if isinstance(value, list):
        return [encode_wire(v) for v in value]
    return value


def decode_wire(value):
    if isinstance(value, dict):
        if set(value)=={'$escaped'}:
            return {k:decode_wire(v) for k,v in value['$escaped']}
        if set(value)=={'$bytes'}:
            return base64.b64decode(value['$bytes'],validate=True)
        return {k:decode_wire(v) for k,v in value.items()}
    if isinstance(value,list):
        return [decode_wire(v) for v in value]
    return value


def loads_wire(text):
    """Decode envelopes bottom-up during JSON parsing, without copying the tree."""
    def object_hook(value):
        if len(value) == 1:
            if '$escaped' in value:
                return dict(value['$escaped'])
            if '$bytes' in value:
                return base64.b64decode(value['$bytes'], validate=True)
        return value
    return json.loads(text, object_hook=object_hook)


def failure(request, status, message):
    return dict(version=PROTOCOL_VERSION,id=request.get('id'),revision=request.get('revision'),
                status=status if status in ("error","cancelled","timeout") else "error",diagnostics=[],error=dict(code='service.'+status,phase='service',message=message))


def _worker(connection, cancelled, compiler, limits, native_executable=None):
    service = None
    try:
        options=Limits(**limits)
        service=(CompilerService(json.loads(Path(compiler).read_text()),limits=options,native_executable=native_executable)
                 if compiler else CompilerService.installed(limits=options))
        connection.send({'ready':True})
        while True:
            directory=connection.recv()
            if directory is None:
                return
            folder=Path(directory)
            request=loads_wire((folder/'request.json').read_text())
            try:
                result=service.request(request,cancelled=cancelled.is_set)
            except Exception as error:
                result=failure(request,'internal',str(error))
            encoded=dump_response(result)
            if len(encoded)>MAX_WIRE_BYTES:
                encoded=dump_response(failure(request,'output-limit','Compiler response exceeds transport budget'))
            (folder/'result.bin').write_bytes(encoded)
            connection.send({'done':True})
    except (EOFError,BrokenPipeError):
        return
    except Exception as error:
        try:
            connection.send({'error':str(error)})
        except (OSError,EOFError):
            pass
    finally:
        if service is not None:
            service.close()
        connection.close()


def _worker_entry(target, args):
    # The supervisor owns this session, including native compiler descendants.
    # Establish it before target code can create any children.
    if os.name == 'posix':
        os.setsid()
    target(*args)


class CompilerWorker:
    """Single-lane reusable child. A killed child is recreated on the next call.

    Caller cancellation is observed while waiting for this lane as well as during
    execution. Only a small control message travels over the pipe; large result
    payloads cannot block recv() past the supervisor deadline.
    """
    def __init__(self, compiler=None, *, limits=None, native_executable=None):
        if native_executable is not None and compiler is None:
            raise ValueError('Native execution requires an explicit matching compiler artifact')
        self.compiler=str(Path(compiler).resolve()) if compiler else None
        self.native_executable=str(Path(native_executable).resolve()) if native_executable else None
        self.limits=limits or Limits()
        self.context=multiprocessing.get_context('spawn')
        self.lock=threading.Lock()
        self.process=None
        self.connection=None
        self.cancelled=None
        self.closed=False

    def _stop(self, *, terminate_group=True):
        if self.process is not None:
            group=self.process.pid if terminate_group and os.name=='posix' else None
            if group is not None:
                try:os.killpg(group,signal.SIGTERM)
                except ProcessLookupError:pass
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(1)
                if self.process.is_alive():
                    self.process.kill();self.process.join(1)
            else:
                self.process.join()
            if group is not None:
                # The leader may have exited before its descendants. Reaping
                # only multiprocessing.Process would leave those children live.
                try:os.killpg(group,signal.SIGKILL)
                except ProcessLookupError:pass
            self.process.close()
        if self.connection is not None:
            self.connection.close()
        self.process=self.connection=self.cancelled=None

    def _wait(self, request, cancelled, deadline, *, cooperative=False):
        cancellation_deadline = None
        while True:
            if cancellation_deadline is None and cancelled():
                self.cancelled.set()
                if not cooperative:
                    self._stop()
                    return failure(request,'cancelled','Compilation cancelled')
                cancellation_deadline = min(deadline, time.monotonic() + CANCEL_GRACE_SECONDS)
            if cancellation_deadline is not None and time.monotonic() >= cancellation_deadline:
                self._stop()
                return failure(request,'cancelled','Compilation cancelled')
            if time.monotonic()>=deadline:
                self._stop()
                return failure(request,'timeout','Compiler process deadline exceeded')
            if self.connection.poll(.025):
                try:
                    message = self.connection.recv()
                    if cancellation_deadline is not None:
                        if message.get('done'):
                            # The child has reset request state and published its reply.
                            # Discard that reply, preserving only the idle process/cache.
                            return {'done': True, 'cancelled': True}
                        self._stop()
                        return failure(request,'cancelled','Compilation cancelled')
                    return message
                except (EOFError,OSError):
                    self._stop()
                    return failure(request,'error','Compiler process closed its channel')
            if not self.process.is_alive():
                self._stop()
                return failure(request,'error','Compiler process exited')

    def request(self, request, *, cancelled=lambda:False, submitted_at=None):
        if not isinstance(request,dict):
            return failure({},'request','Request must be an object')
        started=time.monotonic() if submitted_at is None else submitted_at
        deadline=started+self.limits.seconds
        while not self.lock.acquire(timeout=.025):
            if cancelled():return failure(request,'cancelled','Queued compilation cancelled')
            if time.monotonic()>=deadline:return failure(request,'timeout','Compiler queue deadline exceeded')
        try:
            if self.closed:return failure(request,'error','Compiler worker is closed')
            if time.monotonic()>=deadline:return failure(request,'timeout','Compiler queue deadline exceeded')
            if cancelled():return failure(request,'cancelled','Compilation cancelled')
            encoded=json.dumps(encode_wire(request),ensure_ascii=False).encode()
            if len(encoded)>MAX_WIRE_BYTES:
                return failure(request,'input-limit','Compiler request exceeds transport budget')
            if self.process is None:
                self.connection,child=self.context.Pipe()
                self.cancelled=self.context.Event()
                args=(child,self.cancelled,self.compiler,asdict(self.limits))
                if self.native_executable is not None:args+= (self.native_executable,)
                self.process=self.context.Process(target=_worker_entry,args=(_worker,args))
                self.process.start();child.close()
                ready=self._wait(request,cancelled,deadline)
                if not ready.get('ready'):
                    if self.process is not None:self._stop()
                    return ready if 'status' in ready else failure(request,'error',ready.get('error','Compiler startup failed'))
            self.cancelled.clear()
            with TemporaryDirectory(prefix='pixel-compiler-job-') as directory:
                folder=Path(directory)
                (folder/'request.json').write_bytes(encoded)
                self.connection.send(directory)
                message=self._wait(request,cancelled,deadline,cooperative=True)
                if not message.get('done'):
                    if self.process is not None:self._stop()
                    return message if 'status' in message else failure(request,'error',message.get('error','Compiler request failed'))
                if message.get('cancelled'):
                    return failure(request,'cancelled','Compilation cancelled')
                path=folder/'result.bin'
                if path.stat().st_size>MAX_WIRE_BYTES:
                    self._stop();return failure(request,'output-limit','Compiler response exceeds transport budget')
                result=load_response(path.read_bytes())
                if cancelled():return failure(request,'cancelled','Compilation cancelled')
                if time.monotonic()>=deadline:return failure(request,'timeout','Compiler response deadline exceeded')
                result.setdefault('metrics',{})['supervisedSeconds']=time.monotonic()-started
                return result
        except (ValueError,TypeError,OSError,EOFError) as error:
            self._stop()
            return failure(request,'error',str(error))
        finally:
            self.lock.release()

    def close(self):
        with self.lock:
            self.closed=True
            graceful=False
            if self.process is not None and self.connection is not None:
                try:
                    # Idle workers own and reap their native child in finally.
                    # Let that finish before reaping the worker; signaling its
                    # departed group afterward races group teardown on macOS.
                    self.connection.send(None)
                    self.process.join(1)
                    graceful=self.process.exitcode==0
                except (OSError, EOFError):
                    pass
            self._stop(terminate_group=not graceful)

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
