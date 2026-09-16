"""Command line tools with spatial diagnostics and predictable exit codes."""

import argparse
import json
import sys
from pathlib import Path

from .model import PixelError


def write_json(value, path=None):
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path:
        Path(path).write_text(text)
    else:
        print(text, end="")


def main(command=None, argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    selected = command or (argv[0] if argv else None)
    if selected in ("init", "doctor"):
        from .onboarding import main as onboarding_main
        return onboarding_main(selected, argv if command else argv[1:])
    from .codec import load, save
    from .compiler import compile_file, frontend, normalize
    from .fileaccess import FileAccess
    from .vm import VM
    p = argparse.ArgumentParser(prog=command or "pixellang")
    if command is None:
        p.add_argument(
            "command",
            choices=[
                "compile",
                "run",
                "inspect",
                "format",
                "render",
                "debug",
                "pack",
                "unpack",
                "lock",
                "test",
                "build",
                "init",
                "doctor",
            ],
        )
    p.add_argument("source", type=Path, nargs="?", default=Path("."))
    p.add_argument("-o", "--output", type=Path)
    p.add_argument(
        "--json", action="store_true", help="Machine-readable output and errors"
    )
    p.add_argument("--max-steps", type=int, default=100_000)
    p.add_argument("--trace", type=Path)
    p.add_argument(
        "--read-root",
        type=Path,
        help="Grant program reads inside this existing directory",
    )
    p.add_argument(
        "--write-root",
        type=Path,
        help="Grant program writes inside this existing directory",
    )
    p.add_argument("--input", help="UTF-8 runtime input file, or - for stdin")
    p.add_argument(
        "--scale", type=int, default=8, help="Executable image tile size (4..24)"
    )
    p.add_argument(
        "--force", action="store_true", help="Allow unpack to replace existing files"
    )
    p.add_argument("--no-optimize", action="store_true")
    p.add_argument(
        "--stage",
        choices=["all", "spatial_ast", "canonical_ast", "ir", "bytecode"],
        default="all",
    )
    args = p.parse_args(argv)
    command = command or args.command
    try:
        if command in ("lock", "test", "build") or (
            command == "run"
            and (args.source.is_dir() or args.source.name == "pixel.toml")
        ):
            from .workspace import Workspace

            workspace = Workspace(args.source)
            if command == "lock":
                result = workspace.lock()
            elif command == "build":
                result = workspace.build(args.output)
            elif command == "test":
                result = workspace.test(args.max_steps)
            else:
                data = (
                    sys.stdin.read(1_000_001)
                    if args.input == "-"
                    else Path(args.input).read_text(encoding="utf-8")
                    if args.input
                    else ""
                )
                result = workspace.run(
                    data, args.max_steps, FileAccess(args.read_root, args.write_root)
                )
            if command == "run" and not args.json:
                text = "".join(display(v) + "\n" for v in result["output"])
                if args.output:
                    args.output.write_text(text, encoding="utf-8")
                else:
                    print(text, end="")
            else:
                write_json(result)
            return 1 if result.get("passed") is False else 0
        if command in ("format", "render", "pack", "unpack"):
            if args.output is None:
                p.error("This command requires -o OUTPUT")
            if command in ('pack','render') and args.source.suffix.lower()=='.pxl':
                from .compiler_client import CompilerClient
                from tempfile import NamedTemporaryFile
                with CompilerClient() as client:
                    files,entry=client.read_project(args.source)
                    image=client.request('export-debug-png',files=files,entry=entry,scale=args.scale)['result']['image']
                args.output.parent.mkdir(parents=True,exist_ok=True)
                temporary=None
                try:
                    with NamedTemporaryFile(dir=args.output.parent,delete=False) as stream:
                        temporary=Path(stream.name);stream.write(image)
                    temporary.replace(args.output)
                finally:
                    if temporary is not None:temporary.unlink(missing_ok=True)
                return 0
            native_recovery = command == "unpack" and args.source.suffix.lower() == ".png"
            doc = None if native_recovery else (
                compile_file(args.source).source
                if args.source.suffix.lower() == ".pxl"
                else load(args.source)
            )
            if command == "unpack":
                from .project import project_text, safe_name

                if native_recovery:
                    from .compiler_client import CompilerClient
                    with CompilerClient() as client:
                        recovered = client.request('recover-png',image=args.source.read_bytes())['result']
                else:
                    recovered = project_text(doc)
                root = args.output.resolve()
                targets = [
                    (root / safe_name(name), text)
                    for name, text in recovered["files"].items()
                ]
                for target, _ in targets:
                    if not target.resolve().is_relative_to(root):
                        raise PixelError("io", "Unpack path escapes output directory")
                    if target.exists() and not args.force:
                        raise PixelError(
                            "io", f"Refusing to overwrite {target}; use --force"
                        )
                for target, text in targets:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding="utf-8")
                if args.json:
                    write_json(
                        {
                            "entry": recovered["entry"],
                            "files": [str(t) for t, _ in targets],
                        }
                    )
            elif (
                command == "pack"
                or command == "render"
                and (doc.get("bundle") or args.source.suffix.lower() == ".pxl")
            ):
                from .compiler import compile_source
                from .picture import save_picture

                compile_source(doc)
                save_picture(doc, args.output, args.scale)
            elif command == "format" and args.output.suffix == ".pxl":
                from .project import project_text

                recovered = project_text(doc)
                args.output.write_text(
                    recovered["files"][recovered["entry"]], encoding="utf-8"
                )
            else:
                save(
                    normalize(doc, str(args.source)) if command == "format" else doc,
                    args.output,
                )
        elif (
            command == "inspect"
            and args.stage == "spatial_ast"
            and args.source.suffix.lower() != ".pxl"
        ):
            _, spatial = frontend(load(args.source), str(args.source))
            write_json(spatial, args.output)
        else:
            if command in ("run", "debug") and args.source.suffix == ".pxb":
                bc = json.loads(args.source.read_text())
            elif command in ("compile", "run", "debug") and args.source.suffix.lower() in (".pxl", ".png"):
                from .compiler_client import CompilerClient
                with CompilerClient() as compiler:
                    if args.source.suffix.lower()==".png":
                        bc=compiler.request("compile-png",image=args.source.read_bytes())["result"]["bytecode"]
                    else:
                        files,entry=compiler.read_project(args.source)
                        bc=compiler.request("compile",files=files,entry=entry)["result"]["bytecode"]
            else:
                result = compile_file(args.source, not args.no_optimize)
                bc = result.bytecode
            if command == "compile":
                write_json(bc, args.output or args.source.with_suffix(".pxb"))
            elif command == "inspect":
                inspection = result.inspect()
                write_json(
                    inspection if args.stage == "all" else inspection[args.stage],
                    args.output,
                )
            elif command in ("run", "debug"):
                vm = VM(
                    bc,
                    file_access=FileAccess(args.read_root, args.write_root),
                    max_steps=args.max_steps,
                    trace=bool(args.trace) or command == "debug",
                    input_text=(
                        sys.stdin.read(1_000_001)
                        if args.input == "-"
                        else Path(args.input).read_text(encoding="utf-8")
                    )
                    if args.input
                    else "",
                )
                try:
                    output = vm.run()
                finally:
                    if args.trace:
                        write_json({"state": vm.state(), "trace": vm.trace}, args.trace)
                if command == "debug":
                    write_json({"state": vm.state(), "trace": vm.trace}, args.output)
                elif args.json:
                    write_json({"output": output, "steps": vm.steps}, args.output)
                elif args.output:
                    args.output.write_text("".join(display(v) + "\n" for v in output))
                else:
                    for value in output:
                        print(display(value))
        return 0
    except (PixelError, OSError, ValueError) as e:
        if not isinstance(e, PixelError):
            e = PixelError("io", str(e))
        print(
            json.dumps({"error": e.to_dict()}) if args.json else str(e), file=sys.stderr
        )
        return 1


def compile_main():
    sys.exit(main("compile"))


def project_main():
    sys.exit(main())


def run_main():
    sys.exit(main("run"))


def inspect_main():
    sys.exit(main("inspect"))


def format_main():
    sys.exit(main("format"))


def render_main():
    sys.exit(main("render"))


def debug_main():
    sys.exit(main("debug"))


def display(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).lower() if type(value) is bool else str(value)


def pack_main():
    sys.exit(main("pack"))


def unpack_main():
    sys.exit(main("unpack"))
