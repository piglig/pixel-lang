"""Sequential, bounded framed transport for a stateful native executable."""
import json
import os
from pathlib import Path
import selectors
import struct
import subprocess
import threading
import time

from .model import PixelError


class NativeSessionExecution:
    def __init__(self, executable):
        self.executable = Path(executable).resolve(strict=True)
        self.process = None
        self.sequence = 0
        self.steps = 0
        self.statistics = None
        self.profile_memory = False
        self._lock = threading.Lock()

    def close(self):
        process, self.process = getattr(self, 'process', None), None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=.25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()

    def __del__(self):
        self.close()

    def execute(self, text, *, root, steps, output_chars, file_bytes, cancelled, deadline):
        if not self._lock.acquire(blocking=False):
            raise PixelError('service', 'Native session already has an active request')
        try:
            return self._execute(text, root=root, steps=steps, output_chars=output_chars,
                                 file_bytes=file_bytes, cancelled=cancelled, deadline=deadline)
        except BaseException:
            # No response is committed after interruption. The next request
            # starts with a new process and an empty compiler-owned snapshot.
            self.close()
            raise
        finally:
            self._lock.release()

    def _execute(self, text, *, root, steps, output_chars, file_bytes, cancelled, deadline):
        self.steps, self.statistics = 0, None
        def check():
            if cancelled():
                raise PixelError('service', 'Compilation cancelled', code='service.cancelled')
            if time.monotonic() >= deadline:
                raise PixelError('service', 'Compilation deadline exceeded', code='service.timeout')
        check()
        for value, maximum in ((steps, 3_000_000_000), (output_chars, 64_000_000), (file_bytes, 64_000_000)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('Invalid native session budget')
        payload, directory = text.encode('utf-8'), os.fsencode(Path(root).resolve(strict=True))
        if len(payload) > 4_000_000 or not 1 <= len(directory) <= 4096 or b'\0' in directory:
            raise ValueError('Native session request exceeds transport bounds')
        if self.process is not None and self.process.poll() is not None:
            self.close()
        if self.process is None:
            command = [str(self.executable), '--session', '--buffered-output']
            if self.profile_memory:
                command.append('--memory-profile')
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                os.set_blocking(stream.fileno(), False)
        process = self.process
        self.sequence += 1
        pending = memoryview(struct.pack('>4sQQQQII', b'PXQ1', self.sequence, steps, output_chars,
                                         file_bytes, len(directory), len(payload)) + directory + payload)
        wire_limit = min(384_000_000, output_chars * 6 + 65536)
        output, errors, events = bytearray(), bytearray(), []
        received = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map():
                check()
                for key, _ in selector.select(min(.025, max(0, deadline-time.monotonic()))):
                    stream = key.fileobj
                    if stream is process.stdin:
                        try:
                            count = os.write(stream.fileno(), pending[:65536])
                        except BrokenPipeError:
                            count = len(pending)
                        pending = pending[count:]
                        if not pending:
                            selector.unregister(stream)
                        continue
                    block = os.read(stream.fileno(), 65536)
                    if not block:
                        selector.unregister(stream)
                        continue
                    if stream is process.stderr:
                        if len(errors) + len(block) > 65536:
                            raise PixelError('service', 'Native compiler transport limit exceeded', code='service.output-limit')
                        errors.extend(block)
                        continue
                    received += len(block)
                    if received > wire_limit:
                        raise PixelError('service', 'Native compiler transport limit exceeded', code='service.output-limit')
                    output.extend(block)
                    while b'\n' in output:
                        raw, _, rest = output.partition(b'\n')
                        output = bytearray(rest)
                        marker = raw.startswith(b'\x1e')
                        try:
                            value = json.loads(raw[1:] if marker else raw)
                        except (ValueError, UnicodeError) as error:
                            raise PixelError('service', 'Malformed native session output', code='service.invalid-response') from error
                        if marker:
                            if (pending or output or errors or not isinstance(value, dict)
                                    or type(value.get('nativeSession')) is not int or value.get('nativeSession') != 1
                                    or type(value.get('id')) is not int or value.get('id') != self.sequence
                                    or type(value.get('steps')) is not int or not 0 <= value['steps'] <= steps):
                                raise PixelError('service', 'Invalid native session response boundary', code='service.invalid-response')
                            check()
                            self.steps, self.statistics = value['steps'], value
                            return events
                        events.append(value)
        try:
            stats = json.loads(errors)
            count = stats.get('steps')
            if type(count) is int and 0 <= count <= steps:
                self.steps = count
            message = stats.get('error', 'Native compiler exited before response completion')
        except (ValueError, TypeError, AttributeError):
            message = 'Native compiler exited before response completion'
        code = 'service.steps' if message == 'Instruction budget exceeded' else 'service.native-execution'
        raise PixelError('service', message, code=code)
