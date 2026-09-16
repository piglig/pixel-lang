"""Self-host bytecode transport and reproducible bootstrap runner.

This host harness only transports artifacts and runs the VM after stage0. It does
not implement parsing, checking, lowering, assembly, or semantic-pixel delivery.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

from .vm import VM, validate_bytecode


def read_bytecode_stream(events):
    """Reassemble an ordered, complete protocol-1 stream without changing code."""
    code = None
    diagnostics = None
    ended = False
    instruction_count = 0
    for raw in events:
        event = json.loads(raw)
        if not isinstance(event, dict) or ended:
            raise ValueError("Invalid event or data after stream end")
        kind = event.get("kind")
        if code is None:
            if kind != "header" or event.get("protocol") != 1:
                raise ValueError("Expected protocol-1 header")
            diagnostics = event["diagnostics"]
            if not isinstance(diagnostics, list):
                raise ValueError("Invalid diagnostics")
            code = {key: event[key] for key in (
                "magic", "vm_version", "language_version", "compiler_version", "entry"
            )}
            code.update(records={}, enums={}, constants=[], functions={})
            continue
        if kind == "end":
            expected = dict(records=len(code["records"]), enums=len(code["enums"]), constants=len(code["constants"]),
                            functions=len(code["functions"]), instructions=instruction_count)
            if any(type(event.get(key)) is not int or event[key] != value
                   for key, value in expected.items()):
                raise ValueError("Stream counts do not match delivered program")
            ended = True
        elif diagnostics:
            raise ValueError("Diagnostic stream must not contain executable data")
        elif kind in ("record", "enum", "function"):
            name = event["name"]
            table = code[{"record": "records", "enum": "enums", "function": "functions"}[kind]]
            if not isinstance(name, str) or name in table:
                raise ValueError("Duplicate or invalid declaration")
            if kind == "function":
                table[name] = {key: event[key] for key in ("params", "captures", "locals", "result")}
                table[name]["instructions"] = []
            else:
                table[name] = event["value"]
        elif kind == "constant":
            if event["name"] != str(len(code["constants"])):
                raise ValueError("Constants must be delivered in index order")
            code["constants"].append(event["value"])
        elif kind == "instruction":
            if event["name"] not in code["functions"]:
                raise ValueError("Instruction precedes its function")
            code["functions"][event["name"]]["instructions"].append(event["value"])
            instruction_count += 1
        else:
            raise ValueError("Unknown stream event: " + str(kind))
    if not ended:
        raise ValueError("Incomplete bytecode stream")
    if diagnostics:
        if code["entry"]:
            raise ValueError("Diagnostic stream has an executable entry")
    else:
        validate_bytecode(code)
    return {"bytecode": code, "diagnostics": diagnostics}


def compile_with(compiler, files, *, max_heap_items=16_000_000, max_heap_objects=1_000_000,
                 timeout=None, progress=None):
    if timeout is not None and not 0 < timeout < float('inf'):
        raise ValueError('Timeout must be finite and positive')
    vm = VM(compiler, input_text=json.dumps({"source": "main.pxl", "text": "",
            "operation": "bytecode-stream", "files": files}),
            max_steps=1_000_000_000, max_depth=1024,
            max_heap_items=max_heap_items, max_heap_objects=max_heap_objects,
            max_output_chars=64_000_000)
    with ExitStack() as stack:
        for target in ("pixellang.text.parse_text", "pixellang.semantics.Checker.check",
                       "pixellang.backend.lower", "pixellang.backend.assemble"):
            stack.enter_context(patch(target, side_effect=AssertionError("Host compiler used after stage0")))
        if timeout is None and progress is None:
            events = vm.run()
        else:
            started = time.monotonic()
            while not vm.halted:
                vm.step(snapshot=False)
                if vm.steps % 1_000_000 == 0:
                    if timeout is not None and time.monotonic() - started > timeout:
                        raise TimeoutError(f'Compiler stage exceeded {timeout} seconds')
                    if progress is not None:
                        progress(dict(steps=vm.steps, seconds=time.monotonic() - started))
            events = vm.output
        output = read_bytecode_stream(events)
    if output["diagnostics"]:
        raise ValueError(json.dumps(output["diagnostics"], ensure_ascii=False))
    return output["bytecode"], vm.steps


def normalized(code):
    # Preserve every instruction, constant, type and provenance field; normalize
    # JSON object order and whitespace only. Array order is semantically relevant.
    return json.dumps(code, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generations", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--heap-items", type=int, default=16_000_000)
    parser.add_argument("--heap-objects", type=int, default=1_000_000)
    parser.add_argument("--stage-timeout", type=float, default=300)
    args = parser.parse_args()
    if args.heap_items < 1 or args.heap_objects < 1:
        parser.error("Heap limits must be positive")
    if not 0 < args.stage_timeout < float('inf'):
        parser.error('Stage timeout must be finite and positive')
    root = Path(__file__).resolve().parents[1]
    files = {p.name: p.read_text() for p in sorted((root / "selfhost").glob("*.pxl"))}
    args.output.mkdir(parents=True, exist_ok=False)
    from .regression import fingerprint
    before = fingerprint(root)
    (args.output / 'manifest.json').write_text(json.dumps(dict(
        files=before, generations=args.generations, stageTimeout=args.stage_timeout), indent=2))
    (args.output / "sources.json").write_text(json.dumps(files, ensure_ascii=False, sort_keys=True))
    limits = dict(max_heap_items=args.heap_items, max_heap_objects=args.heap_objects)
    (args.output / "profile.json").write_text(json.dumps(limits, sort_keys=True))
    from .project import compile_project
    print("Building Python stage0", flush=True)
    compiler = compile_project(files)[1].bytecode
    previous = None
    generations = []
    for generation in range(1, args.generations + 1):
        print(f"Compiling stage{generation} from all {len(files)} source files", flush=True)
        started = time.monotonic()
        def progress(event):
            if event['steps'] % 10_000_000 == 0:
                print(json.dumps(dict(generation=generation, **event)), flush=True)
        compiler, steps = compile_with(compiler, files, **limits,
                                       timeout=args.stage_timeout, progress=progress)
        artifact = normalized(compiler)
        (args.output / f"stage{generation}.json").write_text(artifact)
        digest = hashlib.sha256(artifact.encode()).hexdigest()
        print(f"stage{generation}: {steps} steps, {len(artifact)} characters, sha256={digest}", flush=True)
        # Run newly produced code as a compiler immediately, including stage1.
        sample, _ = compile_with(compiler, {"main.pxl": "fn main() { print(6 * 7) }"}, **limits)
        if VM(sample).run() != [42]:
            raise ValueError(f"stage{generation} failed generated-compiler smoke test")
        if previous is not None and previous != artifact:
            raise ValueError(f"stage{generation - 1}/stage{generation} artifacts differ")
        previous = artifact
        generations.append(dict(generation=generation, steps=steps,
                                seconds=time.monotonic() - started, sha256=digest))
        (args.output / 'progress.json').write_text(json.dumps(generations, indent=2))
    unchanged = fingerprint(root) == before
    (args.output / 'summary.json').write_text(json.dumps(dict(
        generations=generations, inputsUnchanged=unchanged, verified=unchanged,
        fixedPointVerified=unchanged and args.generations == 3), indent=2))
    if not unchanged:
        raise ValueError('Verification inputs changed')
    print("Requested bytecode generations verified; PNG delivery is a separate gate.", flush=True)


if __name__ == "__main__":
    main()
