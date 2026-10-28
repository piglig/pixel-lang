"""Explicit file capabilities and bounded, replayable file observations."""

import errno
import codecs
import hashlib
import json
import os
import stat
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .model import PixelError

MAX_TEXT = 1_000_000
MAX_BINARY = 64_000_000
EFFECT_OVERHEAD = 4096


class AccessFailure(OSError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def failure_code(exc):
    if isinstance(exc, AccessFailure):
        return exc.code
    if isinstance(exc, UnicodeError):
        return "io.invalid_encoding"
    return {
        errno.ENOENT: "io.not_found",
        errno.EACCES: "io.access_denied",
        errno.EPERM: "io.access_denied",
        errno.ELOOP: "io.access_denied",
        errno.ENOTDIR: "io.invalid_path",
        errno.EISDIR: "io.invalid_file",
        errno.ENOSPC: "io.no_space",
    }.get(exc.errno, "io.failure")


class FileAccess:
    def __init__(self, read_root=None, write_root=None, budget=8_000_000, *, record=True, limit_total=False):
        self.read_root = Path(read_root).resolve() if read_root is not None else None
        self.write_root = Path(write_root).resolve() if write_root is not None else None
        self.budget = budget
        if type(budget) is not int or not 2 * EFFECT_OVERHEAD <= budget <= 64_000_000:
            raise PixelError("runtime", "Invalid file effect budget")
        self.effects, self.bytes = [], 0
        self.replay_only = False
        if type(record) is not bool:
            raise PixelError("runtime", "Invalid file recording mode")
        self.record = record
        if type(limit_total) is not bool:
            raise PixelError("runtime", "Invalid file accounting mode")
        self.limit_total = limit_total

    def read_chunk(self, path, offset, size):
        if type(offset) is not int or offset < 0 or type(size) is not int or not 4 <= size <= 262144:
            raise AccessFailure("io.invalid_range", "Chunk offset must be nonnegative and size must be 4..262144 bytes")
        with self.parent("read", path) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise AccessFailure("io.invalid_file", "Read requires a regular file")
                if offset > info.st_size:
                    raise AccessFailure("io.invalid_range", "Chunk offset exceeds file size")
                stream.seek(offset)
                raw = stream.read(size)
                eof = len(raw) < size or offset + len(raw) >= info.st_size
                decoder = codecs.getincrementaldecoder("utf-8")("strict")
                text = decoder.decode(raw, final=eof)
                pending, _ = decoder.getstate()
                return {"text": text, "next": offset + len(raw) - len(pending), "done": eof}

    def list_directory(self, path):
        probe = path + "/.pixel-directory" if path else ".pixel-directory"
        entries, size = [], 0
        with self.parent("read", probe) as (parent, _):
            with os.scandir(parent) as stream:
                for entry in stream:
                    directory = entry.is_dir(follow_symlinks=False)
                    if not directory and not entry.is_file(follow_symlinks=False):
                        continue
                    if "\\" in entry.name:
                        raise AccessFailure("io.invalid_path", "Directory contains an unsupported path name")
                    size += len(entry.name.encode("utf-8")) + 64
                    if len(entries) >= 10000 or size > 262144:
                        raise AccessFailure("io.too_large", "Directory listing exceeds its entry budget")
                    entries.append({"name": entry.name, "directory": directory})
        return sorted(entries, key=lambda entry: entry["name"])

    def observe(self, operation, path, value):
        try:
            if operation == "read":
                return self.read(path)
            if operation == "readChunk":
                return self.read_chunk(path, *value)
            if operation == "listDirectory":
                return self.list_directory(path)
            if operation == "readBytes":
                raw = self.read_binary(path, value)
                return list(raw) if self.record else bytearray(raw)
            if operation == "writeBytes":
                return self.write_binary(path, bytes(value))
            if operation == "write":
                return self.write(path, value)
            raise PixelError("runtime", "Unknown file operation")
        except (OSError, UnicodeError) as exc:
            raise PixelError("data", str(exc)[:256].encode("utf-8", "replace").decode("utf-8"),
                             code=failure_code(exc), path=path[:1024].encode("utf-8", "replace").decode("utf-8"),
                             operation=operation) from exc

    @contextmanager
    def parent(self, operation, path):
        if not all(
            hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
        ):
            raise AccessFailure(
                "io.unsupported_host", "Controlled file access requires a POSIX host"
            )
        root = self.read_root if operation == "read" else self.write_root
        if root is None:
            raise AccessFailure(
                "io.access_denied", f"File {operation} capability was not granted"
            )
        if (
            not path
            or len(path) > 1024
            or "\\" in path
            or "\x00" in path
            or path.startswith("/")
        ):
            raise AccessFailure(
                "io.invalid_path",
                "File path must be relative and at most 1024 characters",
            )
        parts = PurePosixPath(path).parts
        if not parts or any(part in ("..", ".") for part in parts):
            raise AccessFailure(
                "io.invalid_path", "File path must stay inside its granted root"
            )
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = child
            yield fd, parts[-1]
        finally:
            os.close(fd)

    def read_binary(self, path, limit):
        if type(limit) is not int or not 0 <= limit <= MAX_BINARY:
            raise AccessFailure("io.too_large", "Invalid binary read limit")
        with self.parent("read", path) as (parent, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise AccessFailure("io.invalid_file", "Read requires a regular file")
                if info.st_size > limit:
                    raise AccessFailure("io.too_large", "File exceeds read limit")
                raw = stream.read(limit + 1)
                if len(raw) > limit:
                    raise AccessFailure("io.too_large", "File exceeds read limit")
                return raw

    def read(self, path):
        text = self.read_binary(path, MAX_TEXT * 4).decode("utf-8")
        if len(text) > MAX_TEXT:
            raise AccessFailure("io.too_large", "File exceeds one million characters")
        return text

    def write(self, path, text):
        if len(text) > MAX_TEXT:
            raise AccessFailure(
                "io.too_large", "File output exceeds one million characters"
            )
        return self.write_binary(path, text.encode("utf-8"))

    def write_binary(self, path, raw):
        if len(raw) > MAX_BINARY:
            raise AccessFailure("io.too_large", "Binary file output exceeds limit")
        with self.parent("write", path) as (parent, name):
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                info = None
            if info is not None and not stat.S_ISREG(info.st_mode):
                raise AccessFailure(
                    "io.invalid_file", "File output must replace a regular file"
                )
            temporary = ".pixel-" + uuid4().hex
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        return True

    @staticmethod
    def cost(effect):
        return (
            EFFECT_OVERHEAD
            + len(json.dumps(effect.get("error"), ensure_ascii=True))
            + (
                len(effect["result"].encode("utf-8"))
                if isinstance(effect.get("result"), str)
                else len(json.dumps(effect["result"], ensure_ascii=False).encode("utf-8"))
                if isinstance(effect.get("result"), dict) or effect.get("operation") == "listDirectory"
                else len(effect["result"]) if isinstance(effect.get("result"), (list, bytes, bytearray)) else 0
            )
        )

    def perform(self, operation, path, text, cursor):
        if not self.record:
            if self.limit_total and self.bytes + 2 * EFFECT_OVERHEAD > self.budget:
                raise PixelError("runtime", "File operation budget exceeded", code="runtime.effect_budget")
            try:
                result = self.observe(operation, path, text)
            except PixelError:
                if self.limit_total:
                    self.bytes += 2 * EFFECT_OVERHEAD
                raise
            if self.limit_total:
                cost = self.cost({"operation": operation, "result": result, "error": None})
                if self.bytes + cost + EFFECT_OVERHEAD > self.budget:
                    raise PixelError("runtime", "File operation budget exceeded", code="runtime.effect_budget")
                self.bytes += cost
            return result
        request = hashlib.sha256(
            json.dumps([operation, path, text], ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        if cursor < len(self.effects):
            effect = self.effects[cursor]
            if effect["request"] != request or effect["operation"] != operation:
                raise PixelError(
                    "runtime", "File replay request differs from recorded execution"
                )
        elif self.replay_only:
            raise PixelError("runtime", "Missing recorded file effect")
        else:
            effect = {
                "operation": operation,
                "path": path[:256].encode("utf-8", "replace").decode("utf-8"),
                "request": request,
                "result": None,
                "error": None,
            }
            if self.bytes + 2 * EFFECT_OVERHEAD > self.budget:
                effect["error"] = {
                    "phase": "runtime",
                    "message": "File effect recording budget exceeded",
                    "code": "runtime.effect_budget",
                    "path": "",
                    "operation": operation,
                }
            else:
                try:
                    effect["result"] = self.observe(operation, path, text)
                except PixelError as exc:
                    if exc.phase != "data":
                        raise
                    effect["error"] = {
                        "phase": "data",
                        "code": exc.code,
                        "path": path[:1024].encode("utf-8", "replace").decode("utf-8"),
                        "operation": operation,
                        "message": exc.message[:256]
                        .encode("utf-8", "replace")
                        .decode("utf-8"),
                    }
                if self.bytes + self.cost(effect) + EFFECT_OVERHEAD > self.budget:
                    effect["result"] = None
                    effect["error"] = {
                        "phase": "runtime",
                        "message": "File effect recording budget exceeded",
                        "code": "runtime.effect_budget",
                        "path": "",
                        "operation": operation,
                    }
            if self.bytes + self.cost(effect) > self.budget:
                raise PixelError("runtime", "File effect recording budget exceeded")
            self.effects.append(effect)
            self.bytes += self.cost(effect)
        if effect["error"]:
            raise PixelError(**effect["error"])
        return deepcopy(effect["result"])

    def replay(self):
        if not self.record:
            raise PixelError("temporal", "File effects were not recorded")
        result = FileAccess(budget=self.budget)
        result.effects = self.effects
        result.replay_only = True
        return result

    def document(self):
        if not self.record:
            raise PixelError("temporal", "File effects were not recorded")
        return {"budget": self.budget, "effects": deepcopy(self.effects)}

    @classmethod
    def restore(cls, document):
        if not isinstance(document, dict) or set(document) != {"budget", "effects"}:
            raise PixelError("temporal", "Invalid file effect document")
        result = cls(budget=document["budget"])
        if (
            not isinstance(document["effects"], list)
            or len(document["effects"]) > 16000
        ):
            raise PixelError("temporal", "Invalid file effect list")
        for effect in document["effects"]:
            if (
                not isinstance(effect, dict)
                or set(effect) != {"operation", "path", "request", "result", "error"}
                or effect["operation"] not in ("read", "write", "readBytes", "writeBytes", "readChunk", "listDirectory")
                or not isinstance(effect["path"], str)
                or len(effect["path"]) > 256
                or not isinstance(effect["request"], str)
                or len(effect["request"]) != 64
            ):
                raise PixelError("temporal", "Invalid recorded file effect")
            error = effect["error"]
            if error is not None:
                if (
                    not isinstance(error, dict)
                    or set(error) != {"phase", "message", "code", "path", "operation"}
                    or error["phase"] not in ("runtime", "data")
                    or not isinstance(error["message"], str)
                    or len(error["message"]) > 256
                    or not isinstance(error["code"], str)
                    or len(error["code"]) > 64
                    or not isinstance(error["path"], str)
                    or len(error["path"]) > 1024
                    or error["operation"] != effect["operation"]
                    or effect["result"] is not None
                ):
                    raise PixelError("temporal", "Invalid recorded file error")
            elif effect["operation"] == "read":
                if (
                    not isinstance(effect["result"], str)
                    or len(effect["result"]) > MAX_TEXT
                ):
                    raise PixelError("temporal", "Invalid recorded file text")
            elif effect["operation"] == "listDirectory":
                value = effect["result"]
                if (not isinstance(value, list) or len(value) > 10000
                        or any(not isinstance(entry, dict) or set(entry) != {"name", "directory"}
                               or not isinstance(entry["name"], str) or not entry["name"]
                               or "/" in entry["name"] or "\\" in entry["name"] or "\x00" in entry["name"]
                               or entry["name"] in (".", "..") or type(entry["directory"]) is not bool
                               for entry in value)):
                    raise PixelError("temporal", "Invalid recorded directory listing")
            elif effect["operation"] == "readChunk":
                value = effect["result"]
                if (not isinstance(value, dict) or set(value) != {"text", "next", "done"}
                        or not isinstance(value["text"], str) or len(value["text"]) > 262144
                        or type(value["next"]) is not int or not 0 <= value["next"] <= (1 << 63) - 1
                        or type(value["done"]) is not bool):
                    raise PixelError("temporal", "Invalid recorded file chunk")
            elif effect["operation"] == "readBytes":
                value = effect["result"]
                if (not isinstance(value, list) or len(value) > MAX_BINARY
                        or any(type(byte) is not int or not 0 <= byte <= 255 for byte in value)):
                    raise PixelError("temporal", "Invalid recorded file bytes")
            elif effect["result"] is not True:
                raise PixelError("temporal", "Invalid recorded write result")
            try:
                effect["path"].encode("utf-8")
                if error is not None:
                    error["message"].encode("utf-8")
                if any(char not in "0123456789abcdef" for char in effect["request"]):
                    raise PixelError("temporal", "Invalid file request digest")
                result.bytes += result.cost(effect)
            except UnicodeError as exc:
                raise PixelError("temporal", "Invalid recorded Unicode text") from exc
            if result.bytes > result.budget:
                raise PixelError("temporal", "Recorded file effects exceed budget")
        result.effects = deepcopy(document["effects"])
        result.replay_only = True
        return result
