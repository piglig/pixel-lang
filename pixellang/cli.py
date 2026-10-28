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


def inspect_program(path, stage):
    """Inspect artifacts produced by the native compiler, without host lowering."""
    from .compiler_client import CompilerClient
    from .vm import validate_bytecode

    suffix = path.suffix.lower()
    if suffix not in ('.pxl', '.png', '.pixel', '.json', '.pxb'):
        raise PixelError('inspect', 'Inspection requires .pxl source, PNG, a pixel document, or .pxb bytecode')
    if suffix != '.pxl' and stage not in ('all', 'bytecode'):
        raise PixelError('inspect', 'This inspection stage requires .pxl source')
    if suffix == '.pxb':
        code = validate_bytecode(json.loads(path.read_text(encoding='utf-8')))
        return {'bytecode': code} if stage == 'all' else code
    with CompilerClient() as client:
        if suffix == '.png':
            code = client.request('compile-png', image=path.read_bytes())['result']['bytecode']
            return {'bytecode': code} if stage == 'all' else code
        if suffix in ('.pixel', '.json'):
            code = client.request('compile-pixels', document=json.loads(path.read_text(encoding='utf-8')),
                                  entry=path.name)['result']['bytecode']
            return {'bytecode': code} if stage == 'all' else code
        files, entry = client.read_project(path)
        operations = {
            'bytecode': ('compile', 'bytecode'),
            'ir': ('inspect-ir', 'ir'),
            'source': ('debug-pixels', 'document'),
            'debug': ('debug-compile', 'debug'),
            'graph': ('native-graphs', 'projection'),
        }
        if stage == 'all':
            from .debug_artifact import prepare_debug
            result = client.request('debug-bundle', files=files, entry=entry, includeIR=True)['result']
            artifact = prepare_debug(result['bytecode'], result['debug'], result['document'])
            artifact.ir = result['ir']
            return artifact.inspect()
        operation, field = operations[stage]
        return client.request(operation, files=files, entry=entry)['result'][field]


def main(command=None, argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    selected = command or (argv[0] if argv else None)
    if selected == "application":
        from .application_cli import main as application_main
        return application_main(argv if command else argv[1:])
    if selected in ("init", "doctor"):
        from .onboarding import main as onboarding_main
        return onboarding_main(selected, argv if command else argv[1:])
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
                "application",
            ],
        )
    p.add_argument("source", type=Path, nargs="?", default=Path("."))
    p.add_argument("-o", "--output", type=Path)
    p.add_argument(
        "--json", action="store_true", help="Machine-readable output and errors"
    )
    p.add_argument("--max-steps", type=int, default=100_000)
    p.add_argument("--filter", help="Select test names using a glob pattern")
    p.add_argument("--list", action="store_true", help="List selected tests without executing them")
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
    p.add_argument("--arg", action="append", default=[], help="Program argument; repeat, using --arg=--option for flags")
    p.add_argument(
        "--scale", type=int, default=8, help="Executable image tile size (4..24)"
    )
    p.add_argument(
        "--force", action="store_true", help="Allow unpack to replace existing files"
    )
    p.add_argument(
        "--stage",
        choices=["all", "source", "debug", "graph", "ir", "bytecode"],
        default="all",
    )
    program_args = []
    if selected in ("run", "debug") and "--" in argv:
        delimiter = argv.index("--")
        argv, program_args = argv[:delimiter], argv[delimiter + 1:]
    args = p.parse_args(argv)
    args.arg.extend(program_args)
    command = command or args.command
    try:
        if command == 'inspect':
            write_json(inspect_program(args.source, args.stage), args.output)
            return 0
        if command == 'format' and args.source.suffix.lower() == '.pxl':
            if args.output is None:
                p.error('This command requires -o OUTPUT')
            from .compiler_client import CompilerClient
            from tempfile import NamedTemporaryFile
            with CompilerClient() as client:
                formatted = client.request('format', files={args.source.name: args.source.read_text(encoding='utf-8')},
                                           entry=args.source.name)['result']['text']
            temporary = None
            try:
                with NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(formatted)
                temporary.replace(args.output)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return 0
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
                result = workspace.test(args.max_steps, pattern=args.filter, list_only=args.list)
            else:
                data = (
                    sys.stdin.read(1_000_001)
                    if args.input == "-"
                    else Path(args.input).read_text(encoding="utf-8")
                    if args.input
                    else ""
                )
                result = workspace.run(
                    data, args.max_steps, FileAccess(args.read_root, args.write_root, record=False), arguments=args.arg
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
            from .compiler_client import CompilerClient
            from .module_paths import safe_name
            from tempfile import NamedTemporaryFile
            with CompilerClient() as client:
                suffix = args.source.suffix.lower()
                if suffix == '.pxl':
                    files, entry = client.read_project(args.source)
                    if command == 'unpack':
                        recovered = {'files': files, 'entry': entry}
                    else:
                        image = client.request('export-debug-png', files=files, entry=entry,
                                               scale=args.scale)['result']['image']
                else:
                    if suffix == '.png':
                        recovered = client.request('recover-png', image=args.source.read_bytes())['result']
                        document = None
                    else:
                        document = json.loads(args.source.read_text(encoding='utf-8'))
                    if command == 'unpack' or command == 'format' and args.output.suffix.lower() == '.pxl':
                        if document is not None:
                            recovered = client.request('recover-pixels', document=document,
                                                       entry=args.source.name)['result']
                    elif command in ('pack', 'render'):
                        if document is None:
                            image = client.request('export-debug-png', files=recovered['files'],
                                                   entry=recovered['entry'], scale=args.scale)['result']['image']
                        else:
                            image = client.request('export-pixel-png', document=document,
                                                   entry=args.source.name, scale=args.scale)['result']['image']
                    else:
                        if document is None:
                            document = client.request('pixels', files=recovered['files'],
                                                      entry=recovered['entry'])['result']['document']
                        else:
                            document = client.request('normalize-pixels', document=document,
                                                      entry=args.source.name)['result']['document']
                        if args.output.suffix.lower() == '.png':
                            image = client.request('export-pixel-png', document=document,
                                                   entry=args.source.name, scale=args.scale)['result']['image']
            if command == 'unpack':
                root = args.output.resolve()
                targets = [(root / safe_name(name), text) for name, text in recovered['files'].items()]
                for target, _ in targets:
                    if not target.resolve().is_relative_to(root):
                        raise PixelError('io', 'Unpack path escapes output directory')
                    if target.exists() and not args.force:
                        raise PixelError('io', f'Refusing to overwrite {target}; use --force')
                for target, text in targets:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding='utf-8')
                if args.json:
                    write_json({'entry': recovered['entry'], 'files': [str(t) for t, _ in targets]})
            else:
                if command == 'format':
                    if args.output.suffix.lower() == '.pxl':
                        if len(recovered['files']) != 1:
                            raise PixelError('io', 'Multiple modules require unpack into a directory')
                        payload = recovered['files'][recovered['entry']].encode('utf-8')
                    elif args.output.suffix.lower() == '.png':
                        payload = image
                    else:
                        payload = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
                else:
                    payload = image
                args.output.parent.mkdir(parents=True, exist_ok=True)
                temporary = None
                try:
                    with NamedTemporaryFile(dir=args.output.parent, delete=False) as stream:
                        temporary = Path(stream.name)
                        stream.write(payload)
                    temporary.replace(args.output)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)

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
                from .compiler_client import CompilerClient
                with CompilerClient() as compiler:
                    bc = compiler.request('compile-pixels',
                        document=json.loads(args.source.read_text(encoding='utf-8')),
                        entry=args.source.name)['result']['bytecode']
            if command == "compile":
                write_json(bc, args.output or args.source.with_suffix(".pxb"))
            elif command in ("run", "debug"):
                vm = VM(
                    bc,
                    arguments=args.arg,
                    file_access=FileAccess(args.read_root, args.write_root, record=bool(args.trace) or command == "debug"),
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
