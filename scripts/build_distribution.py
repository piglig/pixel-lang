"""Build and inspect matching wheel/VSIX deliveries with a checksum manifest."""
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixellang import __version__
from pixellang.native_bundle import load_bundle


def identity():
    release = json.loads((ROOT / "config/release.json").read_text())
    python = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    extension = json.loads((ROOT / "vscode/package.json").read_text())
    if python["version"] != __version__ or __version__ != release["pythonVersion"]:
        raise ValueError("Python package versions disagree")
    if extension["version"] != release["extensionVersion"] or extension["license"] != release["license"]:
        raise ValueError("Extension version or license disagrees with release identity")
    if python["license"] != release["license"] or python["license-files"] != ["LICENSE"]:
        raise ValueError("Python license metadata disagrees with release identity")
    compiler = json.loads((ROOT / "pixellang/artifacts/compiler-manifest.json").read_text())
    load_bundle(ROOT / 'pixellang/artifacts')
    if hashlib.sha256((ROOT / "pixellang/artifacts/compiler.json").read_bytes()).hexdigest() != compiler["sha256"]:
        raise ValueError("Compiler checksum mismatch")
    return {**release, "compilerSha256": compiler["sha256"], "compilerSourceSha256": compiler["sourceSha256"],
            "nativeCompiler":compiler['native']}


def verify_tree(archive, source, prefix, patterns):
    """Reject missing, stale and removed runtime/editor files."""
    expected = set()
    for pattern in patterns:
        for path in sorted(source.glob(pattern)):
            if not path.is_file():
                continue
            name = prefix + path.relative_to(source).as_posix()
            expected.add(name)
            try:
                delivered = archive.read(name)
            except KeyError as error:
                raise ValueError(f"Archive omits {name}") from error
            if delivered != path.read_bytes():
                raise ValueError(f"Archive contains stale {name}")
    for name in archive.namelist():
        if name.startswith(prefix) and not name.endswith("/"):
            relative = Path(name[len(prefix):])
            if any(relative.match(pattern) for pattern in patterns) and name not in expected:
                raise ValueError(f"Archive contains removed {name}")


def build_wheel(output):
    # Setuptools can retain deleted modules in an existing build/lib directory.
    # Build from current sources in a fresh directory without touching local outputs.
    with tempfile.TemporaryDirectory(prefix="pixel-wheel-source-") as directory:
        staging = Path(directory)
        for name in ("pyproject.toml", "setup.py", "README.md", "LICENSE"):
            shutil.copy2(ROOT / name, staging / name)
        shutil.copytree(ROOT / "pixellang", staging / "pixellang",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        subprocess.run(["uv", "build", "--wheel", "--out-dir", str(output)],
                       cwd=staging, check=True)


def verify_archives(wheel, vsix, release):
    license_text = (ROOT / "LICENSE").read_bytes()
    compiler = (ROOT / "pixellang/artifacts/compiler.json").read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata = BytesParser().parsebytes(archive.read(next(name for name in names if name.endswith(".dist-info/METADATA"))))
        if metadata["Version"] != release["pythonVersion"]:
            raise ValueError("Wheel version mismatch")
        if metadata["License-Expression"] != release["license"]:
            raise ValueError("Wheel license metadata mismatch")
        wheel_metadata=BytesParser().parsebytes(archive.read(next(name for name in names if name.endswith('.dist-info/WHEEL'))))
        if (wheel_metadata['Root-Is-Purelib']!='false' or
            wheel_metadata['Tag']!='py3-none-'+release['nativeCompiler']['wheelTag']):
            raise ValueError('Wheel must identify the bundled native platform')
        if not (archive.getinfo('pixellang/artifacts/compiler-native').external_attr>>16)&0o111:
            raise ValueError('Wheel loses native executable permissions')
        if not any(archive.read(name) == license_text for name in names if name.endswith("/LICENSE")):
            raise ValueError("Wheel omits the project license")
        if archive.read("pixellang/artifacts/compiler.json") != compiler:
            raise ValueError("Wheel compiler mismatch")
        if "pixellang/onboarding.py" not in names:
            raise ValueError("Wheel omits onboarding")
        verify_tree(archive, ROOT / "pixellang", "pixellang/",
                    ("*.py", "*.h", "stdlib/*.pxl", "artifacts/*.json", "artifacts/compiler-native"))
    with zipfile.ZipFile(vsix) as archive:
        package = json.loads(archive.read("extension/package.json"))
        if package["version"] != release["extensionVersion"] or package["license"] != release["license"]:
            raise ValueError("VSIX metadata mismatch")
        if not (archive.getinfo('extension/runtime/pixellang/artifacts/compiler-native').external_attr>>16)&0o111:
            raise ValueError('VSIX loses native executable permissions')
        if archive.read("extension/LICENSE.txt") != license_text:
            raise ValueError("VSIX license mismatch")
        if archive.read("extension/runtime/pixellang/artifacts/compiler.json") != compiler:
            raise ValueError("VSIX compiler mismatch")
        if archive.read("extension/media/icon.png") != (ROOT / "assets/branding/icon.png").read_bytes():
            raise ValueError("VSIX icon mismatch")
        verify_tree(archive, ROOT / "pixellang", "extension/runtime/pixellang/",
                    ("*.py", "*.h", "stdlib/*.pxl", "artifacts/*.json", "artifacts/compiler-native"))
        verify_tree(archive, ROOT / "vscode", "extension/",
                    ("src/**/*.js", "media/*", "syntaxes/*.json", "language-configuration.json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error("Use a fresh output directory outside the checkout")
    release = identity()
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run(["npm", "run", "prepare-runtime"], cwd=ROOT / "vscode", check=True)
    from scripts.verify_preview import inputs
    frozen = inputs()
    build_wheel(output)
    vsix = output / f"pixellang-studio-{release['extensionVersion']}.vsix"
    native=release['nativeCompiler']
    target={('darwin','arm64'):'darwin-arm64',('darwin','x86_64'):'darwin-x64',
            ('linux','x86_64'):'linux-x64',('linux','aarch64'):'linux-arm64'}[(native['platform'],native['machine'])]
    subprocess.run([str(ROOT / "vscode/node_modules/.bin/vsce"), "package", "--pre-release", "--target", target, "--out", str(vsix)], cwd=ROOT / "vscode", check=True)
    wheel, = output.glob("*.whl")
    verify_archives(wheel, vsix, release)
    if inputs() != frozen:
        raise ValueError("Distribution inputs changed during packaging")
    release["inputs"] = frozen
    release["gitCommit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    release["dirtyCheckout"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    release["artifacts"] = {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size} for p in (wheel, vsix)}
    (output / "release.json").write_text(json.dumps(release, indent=2) + "\n")
    names = sorted([wheel, vsix, output / "release.json"])
    (output / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in names))
    print(json.dumps(release, indent=2))


if __name__ == "__main__":
    main()
