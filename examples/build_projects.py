"""Refresh the deliverable executable image from the data-report project."""

from pathlib import Path

from pixellang.compiler_client import CompilerClient

ROOT = Path(__file__).resolve().parents[1]


def main():
    with CompilerClient() as compiler:
        files, entry = compiler.read_project(ROOT / "examples/data-report/main.pxl")
        image = compiler.request('export-png', files=files, entry=entry, scale=8)['result']['image']
        (ROOT / "examples/data-report/program.png").write_bytes(image)


if __name__ == "__main__":
    main()
