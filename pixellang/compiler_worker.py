"""Persistent, supervised compiler process; cancellation can terminate primitives."""
from dataclasses import asdict
import base64
import json
import multiprocessing
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time

from .compiler_service import CompilerService, Limits, PROTOCOL_VERSION

MAX_WIRE_BYTES = 96_000_000


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


def failure(request, status, message):
    return dict(version=PROTOCOL_VERSION,id=request.get('id'),revision=request.get('revision'),
                status=status if status in ("error","cancelled","timeout") else "error",diagnostics=[],error=dict(code='service.'+status,phase='service',message=message))


def _worker(connection, cancelled, compiler, limits):
    try:
        options=Limits(**limits)
        service=(CompilerService(json.loads(Path(compiler).read_text()),limits=options)
                 if compiler else CompilerService.installed(limits=options))
        connection.send({'ready':True})
        while True:
            directory=connection.recv()
            if directory is None:
                return
            folder=Path(directory)
            request=decode_wire(json.loads((folder/'request.json').read_text()))
            try:
                result=service.request(request,cancelled=cancelled.is_set)
            except Exception as error:
                result=failure(request,'internal',str(error))
            encoded=json.dumps(encode_wire(result),ensure_ascii=False).encode()
            if len(encoded)>MAX_WIRE_BYTES:
                encoded=json.dumps(failure(request,'output-limit','Compiler response exceeds transport budget')).encode()
            (folder/'result.json').write_bytes(encoded)
            connection.send({'done':True})
    except (EOFError,BrokenPipeError):
        return
    except Exception as error:
        try:
            connection.send({'error':str(error)})
        except (OSError,EOFError):
            pass
    finally:
        connection.close()


class CompilerWorker:
    """Single-lane reusable child. A killed child is recreated on the next call.

    Caller cancellation is observed while waiting for this lane as well as during
    execution. Only a small control message travels over the pipe; large result
    payloads cannot block recv() past the supervisor deadline.
    """
    def __init__(self, compiler=None, *, limits=None):
        self.compiler=str(Path(compiler).resolve()) if compiler else None
        self.limits=limits or Limits()
        self.context=multiprocessing.get_context('spawn')
        self.lock=threading.Lock()
        self.process=None
        self.connection=None
        self.cancelled=None
        self.closed=False

    def _stop(self):
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(1)
                if self.process.is_alive():
                    self.process.kill();self.process.join(1)
            else:
                self.process.join()
            self.process.close()
        if self.connection is not None:
            self.connection.close()
        self.process=self.connection=self.cancelled=None

    def _wait(self, request, cancelled, deadline):
        while True:
            if cancelled():
                self.cancelled.set()
                # Termination is intentional: no late result may escape cancellation.
                self._stop()
                return failure(request,'cancelled','Compilation cancelled')
            if time.monotonic()>=deadline:
                self._stop()
                return failure(request,'timeout','Compiler process deadline exceeded')
            if self.connection.poll(.025):
                try:
                    return self.connection.recv()
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
                self.process=self.context.Process(target=_worker,args=(child,self.cancelled,self.compiler,asdict(self.limits)))
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
                message=self._wait(request,cancelled,deadline)
                if not message.get('done'):
                    if self.process is not None:self._stop()
                    return message if 'status' in message else failure(request,'error',message.get('error','Compiler request failed'))
                path=folder/'result.json'
                if path.stat().st_size>MAX_WIRE_BYTES:
                    self._stop();return failure(request,'output-limit','Compiler response exceeds transport budget')
                result=decode_wire(json.loads(path.read_text()))
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
            self._stop()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
