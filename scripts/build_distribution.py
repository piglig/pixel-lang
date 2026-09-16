"""Build and inspect matching wheel/VSIX deliveries with a checksum manifest."""
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixellang import __version__


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
    if hashlib.sha256((ROOT / "pixellang/artifacts/compiler.json").read_bytes()).hexdigest() != compiler["sha256"]:
        raise ValueError("Compiler checksum mismatch")
    return {**release, "compilerSha256": compiler["sha256"], "compilerSourceSha256": compiler["sourceSha256"]}


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
        if not any(archive.read(name) == license_text for name in names if name.endswith("/LICENSE")):
            raise ValueError("Wheel omits the project license")
        if archive.read("pixellang/artifacts/compiler.json") != compiler:
            raise ValueError("Wheel compiler mismatch")
        if "pixellang/onboarding.py" not in names:
            raise ValueError("Wheel omits onboarding")
    with zipfile.ZipFile(vsix) as archive:
        package = json.loads(archive.read("extension/package.json"))
        if package["version"] != release["extensionVersion"] or package["license"] != release["license"]:
            raise ValueError("VSIX metadata mismatch")
        if archive.read("extension/LICENSE.txt") != license_text:
            raise ValueError("VSIX license mismatch")
        if archive.read("extension/runtime/pixellang/artifacts/compiler.json") != compiler:
            raise ValueError("VSIX compiler mismatch")
        if archive.read("extension/media/icon.png") != (ROOT / "assets/branding/icon.png").read_bytes():
            raise ValueError("VSIX icon mismatch")


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
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(output)], cwd=ROOT, check=True)
    vsix = output / f"pixellang-studio-{release['extensionVersion']}.vsix"
    subprocess.run([str(ROOT / "vscode/node_modules/.bin/vsce"), "package", "--pre-release", "--out", str(vsix)], cwd=ROOT / "vscode", check=True)
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
