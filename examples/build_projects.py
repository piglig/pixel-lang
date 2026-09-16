"""Refresh the deliverable executable image from the data-report project."""

from pathlib import Path

from pixellang.picture import save_picture
from pixellang.project import compile_project, read_project

ROOT = Path(__file__).resolve().parents[1]


def main():
    files, entry = read_project(ROOT / "examples/data-report/main.pxl")
    source, _ = compile_project(files, entry)
    save_picture(source, ROOT / "examples/data-report/program.png", scale=8)


if __name__ == "__main__":
    main()
