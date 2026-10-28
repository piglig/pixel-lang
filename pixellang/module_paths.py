"""Logical module path validation, independent of language parsing."""

import posixpath

from .model import PixelError


def safe_name(name):
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
    ):
        raise PixelError("project", "Project paths must be relative POSIX paths")
    normalized = posixpath.normpath(name)
    if (
        normalized == ".."
        or normalized.startswith("../")
        or not normalized.endswith(".pxl")
    ):
        raise PixelError(
            "project", "Module path must stay inside the project and end in .pxl"
        )
    return normalized
