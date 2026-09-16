"""Manifest projects, content-locked local libraries and reproducible image builds."""

import hashlib
import json
import re
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory, NamedTemporaryFile

from .fileaccess import FileAccess
from .model import PixelError
from .compiler_client import CompilerClient, CompiledArtifact
from .project import safe_name
from .vm import VM

MANIFEST = "pixel.toml"
LOCK = "pixel.lock"
SKIP = {".git", ".venv", "node_modules", "dist", "__pycache__"}


def fail(message):
    raise PixelError("project", message)


def canonical(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def inside(root, name):
    root = Path(root).resolve()
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or Path(name).is_absolute()
    ):
        fail("Expected a relative project path")
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        fail(f"Path escapes project root: {name}")
    return path


def read_text(path):
    if path.stat().st_size > 4_000_000:
        fail(f"File exceeds 4 MB: {path.name}")
    # Preserve line endings so integrity hashes describe actual UTF-8 file bytes.
    with path.open(encoding="utf-8", newline="") as stream:
        return stream.read()


class Workspace:
    """Logical module names contain no absolute paths or machine-specific state.

    Each dependency is a directory containing pixel.toml. Its source appears at
    deps/<alias>/..., including its own nested deps namespace. Library manifests
    are locked alongside every .pxl file. Updating a lock is always explicit.
    """

    def __init__(self, path="."):
        path = Path(path).resolve()
        self.root = path if path.is_dir() else path.parent
        if path.is_file() and path.name != MANIFEST:
            fail(f"Project manifest must be named {MANIFEST}")
        self.files = {}
        self.source_paths = {}
        self.manifest_paths = []
        self.dependencies = {}
        self.config = self._load(self.root, "", set())
        self.entry = safe_name(self.config["project"].get("entry", "main.pxl"))
        if self.entry not in self.files:
            fail(f"Entry module not found: {self.entry}")
        tests = self.config.get("test", [])
        if not isinstance(tests, list):
            fail("Tests must use [[test]] tables")
        self.tests = []
        for item in tests:
            if not isinstance(item, dict) or set(item) - {
                "name",
                "entry",
                "input",
                "expected",
                "read_root",
                "written",
            }:
                fail(
                    "Invalid test table; allowed keys: name, entry, input, expected, read_root, written"
                )
            entry = safe_name(item.get("entry"))
            if entry not in self.files or entry.startswith("deps/"):
                fail(f"Test entry not found in this project: {entry}")
            for key in ("input", "expected", "read_root"):
                if key in item:
                    inside(self.root, item[key])
            if "name" in item and not isinstance(item["name"], str):
                fail("Test name must be a string")
            if "written" in item:
                if not isinstance(item["written"], dict):
                    fail("written must map output paths to expected fixture paths")
                for target, expected in item["written"].items():
                    inside(self.root, target)
                    inside(self.root, expected)
            self.tests.append(dict(item))
        # Assertion-based test programs require no explicit manifest entry.
        explicit = {item["entry"] for item in self.tests}
        self.tests.extend(
            {"entry": name}
            for name in sorted(self.files)
            if name.startswith("tests/")
            and name.endswith("_test.pxl")
            and name not in explicit
        )

    def _load(self, root, prefix, visiting):
        if root in visiting:
            fail("Cyclic local project dependency")
        if len(visiting) >= 16:
            fail("Dependency nesting exceeds 16 projects")
        manifest_path = root / MANIFEST
        self.manifest_paths.append(str(manifest_path))
        raw = read_text(manifest_path)
        try:
            config = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as exc:
            fail(f"Invalid {prefix}{MANIFEST}: {exc}")
        if set(config) - {"format", "project", "dependencies", "test"}:
            fail(f"Unknown manifest section in {prefix}{MANIFEST}")
        if type(config.get("format")) is not int or config["format"] != 1:
            fail("Manifest format must be 1")
        project = config.get("project")
        if not isinstance(project, dict) or set(project) - {"name", "entry"}:
            fail("[project] accepts name and entry")
        if not isinstance(project.get("name"), str) or not project["name"].strip():
            fail("[project] requires a name")
        safe_name(project.get("entry", "main.pxl"))
        dependencies = config.get("dependencies", {})
        if not isinstance(dependencies, dict):
            fail("[dependencies] must map aliases to local project directories")
        hashes = {MANIFEST: hashlib.sha256(raw.encode("utf-8")).hexdigest()}
        for source in sorted(root.rglob("*.pxl")):
            rel = source.relative_to(root)
            if any(part in SKIP or part.startswith(".") for part in rel.parts):
                continue
            if rel.parts[0] == "deps":
                fail(
                    "deps/ is reserved for dependency mounts; move local sources elsewhere"
                )
            if not source.resolve().is_relative_to(root):
                fail(f"Source symlink escapes its project: {rel.as_posix()}")
            name = safe_name(prefix + rel.as_posix())
            if name in self.files:
                fail(f"Duplicate module: {name}")
            text = read_text(source)
            self.files[name] = text
            self.source_paths[name] = str(source.resolve())
            hashes[rel.as_posix()] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if len(self.files) > 128:
                fail("Project and dependencies exceed 128 source files")
        if prefix:
            self.dependencies[prefix.rstrip("/")] = hashes
        for alias, location in sorted(dependencies.items()):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", alias) or not isinstance(
                location, str
            ):
                fail("Dependencies require a simple alias and a local path string")
            if Path(location).is_absolute():
                fail("Dependency locations must be relative paths")
            target = (root / location).resolve()
            # Explicit dependencies may live outside the project; imports may not.
            if target == root or target.is_relative_to(root):
                fail(
                    "Local dependency projects must be outside their parent's source root"
                )
            self._load(target, prefix + "deps/" + alias + "/", visiting | {root})
        return config

    def lock_document(self):
        return {
            "format": "pixellang-lock",
            "version": 1,
            "dependencies": self.dependencies,
        }

    def lock(self):
        path = self.root / LOCK
        path.write_text(canonical(self.lock_document()), encoding="utf-8")
        return {"lock": str(path), "dependencies": len(self.dependencies)}

    def verify_lock(self):
        path = self.root / LOCK
        if not path.exists():
            if self.dependencies:
                fail("Dependencies are not locked; run pixel lock")
            return
        try:
            actual = json.loads(read_text(path))
        except (ValueError, UnicodeError):
            fail("Invalid pixel.lock; run pixel lock to explicitly regenerate it")
        if actual != self.lock_document():
            fail(
                "Dependency contents differ from pixel.lock; review changes and run pixel lock"
            )

    def compile(self, entry=None):
        self.verify_lock()
        with CompilerClient() as compiler:
            source=compiler.request("pixels",files=self.files,entry=entry or self.entry)["result"]["document"]
            code=compiler.request("compile",files=self.files,entry=entry or self.entry)["result"]["bytecode"]
            return source,CompiledArtifact(code,source)

    def build(self, output=None, *, cancelled=lambda:False, submitted_at=None):
        self.verify_lock()
        path = Path(output).resolve() if output else self.root / "dist" / "program.png"
        if path.suffix.lower() != ".png":
            fail("Project build output must be a .png file")
        if path == self.root / MANIFEST or path in {
            (self.root / n).resolve() for n in self.files
        }:
            fail("Build output cannot replace a project input")
        with CompilerClient() as compiler:
            image=compiler.request("export-png",files=self.files,entry=self.entry,cancelled=cancelled,submitted_at=submitted_at)["result"]["image"]
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent,prefix=path.name+".",suffix=".tmp",delete=False) as stream:
            temporary=Path(stream.name)
            stream.write(image)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "image": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def run(self, input_text="", max_steps=100_000, file_access=None):
        self.verify_lock()
        with CompilerClient() as compiler:
            code=compiler.request("compile",files=self.files,entry=self.entry)["result"]["bytecode"]
        vm = VM(
            code,
            input_text=input_text,
            max_steps=max_steps,
            file_access=file_access,
        )
        return {"output": vm.run(), "steps": vm.steps}

    def test(self, max_steps=100_000):
        self.verify_lock()
        if not self.tests:
            fail("No tests found; add tests/*_test.pxl or [[test]] entries")
        results = []
        with CompilerClient() as compiler:
            return self._test_with_compiler(compiler,max_steps)

    def _test_with_compiler(self, compiler, max_steps):
        results=[]
        for case in self.tests:
            item = {"name": case.get("name", case["entry"]), "entry": case["entry"]}
            try:
                code=compiler.request("compile",files=self.files,entry=case["entry"])["result"]["bytecode"]
                input_text = (
                    read_text(inside(self.root, case["input"]))
                    if "input" in case
                    else ""
                )
                with TemporaryDirectory(prefix="pixel-project-test-") as directory:
                    access = FileAccess(
                        read_root=inside(self.root, case["read_root"])
                        if "read_root" in case
                        else None,
                        write_root=directory if case.get("written") else None,
                    )
                    vm = VM(
                        code,
                        input_text=input_text,
                        max_steps=max_steps,
                        file_access=access,
                    )
                    output = vm.run()
                    if "expected" in case:
                        expected = json.loads(
                            read_text(inside(self.root, case["expected"]))
                        )
                        if canonical(output) != canonical(expected):
                            fail(
                                f"Output differs: expected {expected!r}, received {output!r}"
                            )
                    for target, fixture in case.get("written", {}).items():
                        actual = inside(Path(directory), target)
                        if (
                            actual.read_bytes()
                            != inside(self.root, fixture).read_bytes()
                        ):
                            fail(f"Written file differs from fixture: {target}")
                item.update(passed=True, steps=vm.steps)
            except (PixelError, OSError, ValueError) as exc:
                item.update(passed=False, error=str(exc))
            results.append(item)
        return {"passed": all(r["passed"] for r in results), "tests": results}
