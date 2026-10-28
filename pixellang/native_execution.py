"""Bounded, cancellable subprocess lane for a prebuilt native compiler.

This adapter never builds an artifact or falls back to interpreted execution.
The caller is responsible for selecting a compiler matching its source artifact.
"""
import json
import os
from pathlib import Path
import selectors
import subprocess
import time

from .model import PixelError


class NativeExecution:
    def __init__(self, executable):
        self.executable = Path(executable).resolve(strict=True)
        self.steps = 0
        self.statistics = None
        self.profile_memory = False

    def execute(self, text, *, root, steps, output_chars, file_bytes, cancelled, deadline):
        self.steps = 0
        self.statistics = None
        def check():
            if cancelled():
                raise PixelError('service', 'Compilation cancelled', code='service.cancelled')
            if time.monotonic() >= deadline:
                raise PixelError('service', 'Compilation deadline exceeded', code='service.timeout')
        check()
        command = [str(self.executable), '--max-steps', str(steps), '--output-budget',
                   str(output_chars), '--io-budget', str(file_bytes), '--report-stats', '--buffered-output',
                   '--read-root', str(root), '--write-root', str(root)]
        if self.profile_memory:command.append('--memory-profile')
        # Wire bytes have a separate bound: escaping can expand language values.
        wire_limit = min(384_000_000, output_chars * 6 + 65536)
        output = bytearray()
        errors = bytearray()
        pending = memoryview(text.encode('utf-8'))
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        try:
            with selectors.DefaultSelector() as selector:
                for stream, mode in ((process.stdin, selectors.EVENT_WRITE),
                                     (process.stdout, selectors.EVENT_READ),
                                     (process.stderr, selectors.EVENT_READ)):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, mode)
                while selector.get_map():
                    check()
                    for key, _ in selector.select(min(.025, max(0, deadline-time.monotonic()))):
                        stream = key.fileobj
                        if stream is process.stdin:
                            try:
                                count = os.write(stream.fileno(), pending[:65536]) if pending else 0
                                pending = pending[count:]
                            except BrokenPipeError:
                                pending = pending[:0]
                            if not pending:
                                selector.unregister(stream)
                                stream.close()
                        else:
                            block = os.read(stream.fileno(), 65536)
                            if not block:
                                selector.unregister(stream)
                                stream.close()
                                continue
                            target = output if stream is process.stdout else errors
                            limit = wire_limit if stream is process.stdout else 65536
                            if len(target) + len(block) > limit:
                                raise PixelError('service', 'Native compiler transport limit exceeded',
                                                 code='service.output-limit')
                            target.extend(block)
                while process.poll() is None:
                    check()
                    try:
                        process.wait(timeout=.025)
                    except subprocess.TimeoutExpired:
                        pass
            check()
            try:
                stats = json.loads(errors)
                count = stats['steps']
                if type(count) is not int or not 0 <= count <= steps:
                    raise ValueError('Invalid native instruction count')
                self.steps = count
                self.statistics = stats
            except (ValueError, KeyError, TypeError) as error:
                raise PixelError('service', 'Missing or invalid native execution statistics',
                                 code='service.invalid-response') from error
            if process.returncode != 0:
                message = stats.get('error', 'Native compiler failed')
                code = 'service.steps' if message == 'Instruction budget exceeded' else 'service.native-execution'
                raise PixelError('service', message, code=code)
            if stats.get('nativeStats') != 1:
                raise PixelError('service', 'Missing native success marker', code='service.invalid-response')
            try:
                events=[]
                for index,line in enumerate(output.decode('utf-8').splitlines()):
                    if index % 128 == 0:
                        check()
                    events.append(json.loads(line))
                check()
                return events
            except (ValueError, UnicodeError) as error:
                raise PixelError('service', 'Malformed native compiler output',
                                 code='service.invalid-response') from error
        finally:
            # Reap the exact child on every path, including callback exceptions.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=.25)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            # A cooperative cancellation may have reported its last instruction
            # before shutdown. Missing statistics never become fabricated work.
            if not process.stderr.closed:
                try:
                    tail=process.stderr.read(65536-len(errors))
                    if tail:
                        errors.extend(tail)
                except OSError:
                    pass
            try:
                count=json.loads(errors)['steps']
                if type(count) is int and 0 <= count <= steps:
                    self.steps=count
            except (ValueError, KeyError, TypeError):
                pass
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
