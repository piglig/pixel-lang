"""Dependency-light project creation and installation diagnostics."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys

from . import __version__


def create_project(destination, name=None):
    root = Path(destination).absolute()
    name = root.name if name is None else name
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("Project name must start with a letter and contain at most 64 letters, digits, '-' or '_'.")
    if root.is_symlink():
        raise ValueError("Project destination must not be a symbolic link")
    existed = root.exists()
    if existed and (not root.is_dir() or any(root.iterdir())):
        raise ValueError("Project destination must be an empty directory; existing files will not be replaced")
    files = {
        "pixel.toml": f'format = 1\n\n[project]\nname = "{name}"\nentry = "main.pxl"\n\n[[test]]\nname = "greeting"\nentry = "main.pxl"\nexpected = "expected.json"\n',
        "main.pxl": 'fn main() {\n    print("Hello, PixelLang!")\n}\n',
        "expected.json": '["Hello, PixelLang!"]\n',
        ".gitignore": "dist/\n.venv/\n",
        "README.md": f"# {name}\n\n```sh\npixel run .\npixel test .\npixel build .\n```\n\nEdit `main.pxl` and update `expected.json` when the expected output changes.\n",
    }
    # Exclusive creation also protects against files appearing after the check.
    created = []
    root.mkdir(exist_ok=existed)
    try:
        for filename, text in files.items():
            target = root / filename
            with target.open("x", encoding="utf-8") as stream:
                created.append(target)
                stream.write(text)
    except OSError:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        if not existed:
            try:
                root.rmdir()
            except OSError:
                pass
        raise
    return {"passed": True, "root": str(root), "name": name, "files": list(files)}


def diagnose():
    checks = []

    def record(name, passed, detail, fix=""):
        checks.append({"name": name, "passed": bool(passed), "detail": detail, "fix": fix if not passed else ""})

    record("python", sys.version_info >= (3, 11), f"{sys.version.split()[0]} ({sys.executable})", "Install Python 3.11 or newer and recreate your virtual environment.")
    record("host", all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")), sys.platform, "Use a POSIX host such as Linux or macOS; controlled compiler file access requires POSIX support.")
    try:
        import PIL
        from PIL import Image
        major = int(PIL.__version__.split(".")[0])
        # Exercise the codec too: an import alone does not verify PNG support.
        from io import BytesIO
        stream = BytesIO()
        Image.new("RGBA", (1, 1)).save(stream, format="PNG")
        stream.seek(0)
        with Image.open(stream) as image:
            image.load()
        record("pillow", 10 <= major < 13, PIL.__version__, "Install Pillow>=10,<13 using this Python interpreter's -m pip command.")
    except (ImportError, OSError, ValueError) as exc:
        record("pillow", False, str(exc), "Install Pillow>=10,<13 using this Python interpreter's -m pip command.")
    artifacts = Path(__file__).parent / "artifacts"
    try:
        manifest = json.loads((artifacts / "compiler-manifest.json").read_text(encoding="utf-8"))
        hasher = hashlib.sha256()
        with (artifacts / "compiler.json").open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        record("compiler", digest == manifest["sha256"], digest, "Reinstall PixelLang from a verified wheel; the bundled compiler does not match its manifest.")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record("compiler", False, str(exc), "Reinstall PixelLang from a verified wheel to restore the compiler and manifest.")
    return {"passed": all(check["passed"] for check in checks), "version": __version__, "python": sys.executable, "checks": checks}


def main(command, argv):
    parser = argparse.ArgumentParser(prog=f"pixel {command}")
    parser.add_argument("--json", action="store_true")
    if command == "init":
        parser.add_argument("destination", nargs="?", default=".")
        parser.add_argument("--name")
    args = parser.parse_args(argv)
    try:
        result = create_project(args.destination, args.name) if command == "init" else diagnose()
    except (OSError, ValueError) as exc:
        result = {"passed": False, "error": str(exc)}
    if args.json:
        print(json.dumps(result, indent=2))
    elif "error" in result:
        print(result["error"], file=sys.stderr)
    elif command == "init":
        print(f"Created {result['root']}\nNext: pixel run {shlex.quote(result['root'])}\nThen: pixel test {shlex.quote(result['root'])}")
    else:
        for check in result["checks"]:
            print(f"{'OK' if check['passed'] else 'FAIL'} {check['name']}: {check['detail']}")
            if check["fix"]:
                print(f"  {check['fix']}")
    return 0 if result["passed"] else 1
