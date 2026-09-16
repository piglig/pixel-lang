"""Local JSON-lines service for editor hosts. No HTTP server or host-code execution."""

import base64
import io
import json
import sys
import signal
import threading
import time
import weakref
from uuid import uuid4


from .compiler_client import CompilerClient
from .compiler_rpc import serve
from .compiler_worker import failure
from .cli import display
from .debugvalues import display_type, inspect_values
from .editor import SymbolIndex, complete, format_source
from .fileaccess import FileAccess
from .model import Node, PixelError
from .picture import encode_picture
from .project import builtin_source, compile_project, project_text
from .spatialedit import SpatialProject
from .temporal import Timeline
from .workspace import Workspace


class Workstation:
    def __init__(self):
        self.sessions = {}
        self.compiler = CompilerClient()
        self._finalizer = weakref.finalize(self,self.compiler.close)

    def close(self):
        self._finalizer()

    def request(self, method, args, *, cancelled=lambda:False, submitted_at=None):
        if method == "check":
            checked=self.compiler.request("check",files=args["files"],entry=args.get("entry","main.pxl"),
                                          cancelled=cancelled,revision=args.get("revision"),submitted_at=submitted_at)
            return {"ok":True,"metrics":checked["metrics"],"compilerSha256":checked["compilerSha256"]}

        if method in ("spatial-inspect", "spatial-edit"):
            project = SpatialProject(args["files"], args.get("entry", "main.pxl"))
            return (
                project.describe(args["source"])
                if method == "spatial-inspect"
                else project.edit(args)
            )
        if method in ("project-test", "project-build", "project-lock"):
            workspace = Workspace(args["root"])
            if method == "project-test":
                return workspace.test(args.get("max_steps", 100_000))
            if method == "project-build":
                return workspace.build(cancelled=cancelled,submitted_at=submitted_at)
            return workspace.lock()

        if method == "workspace":
            workspace = Workspace(args["root"])
            lock_error = None
            try:
                workspace.verify_lock()
            except PixelError as error:
                lock_error = error.message
            return {
                "root": str(workspace.root),
                "entry": workspace.entry,
                "files": workspace.files,
                "paths": workspace.source_paths,
                "manifests": workspace.manifest_paths,
                "lock_error": lock_error,
            }

        if method == "completion":
            return complete(
                args["files"],
                args.get("entry", "main.pxl"),
                args["source"],
                args["offset"],
            )
        if method in ("definition", "references", "rename"):
            index = SymbolIndex(
                args["files"],
                args.get("entry", "main.pxl"),
                tolerant=method != "rename",
            )
            if method == "rename":
                return index.rename(args["source"], args["offset"], args["new_name"])
            result = (
                index.definition(args["source"], args["offset"])
                if method == "definition"
                else index.references(
                    args["source"],
                    args["offset"],
                    args.get("include_declaration", True),
                )
            )
            if args.get("with_sources"):
                return {"result": result, "files": index.project["sources"]}
            return result
        if method == "recover":
            image = base64.b64decode(args["image"], validate=True)
            return self.compiler.request('recover-png',image=image,cancelled=cancelled,
                                         submitted_at=submitted_at)['result']
        if method == "format":
            compile_project(args["files"],args.get("entry","main.pxl"))
            return {"entry":args.get("entry","main.pxl"),"files":{name:format_source(text,name) for name,text in args["files"].items()}}
        if method == "build":
            if args.get("lock_error"):raise PixelError("project",args["lock_error"])
            submitted_at=time.monotonic() if submitted_at is None else submitted_at
            entry=args.get("entry","main.pxl")
            compiled=self.compiler.debug_project(args["files"],entry,cancelled=cancelled,submitted_at=submitted_at,
                                                 include_ir=False,include_png=True,scale=args.get("scale",8))
            image=compiled.image
            timeline=Timeline(compiled.source,args.get("input",""),args.get("max_steps",100_000),
                checkpoint_interval=args.get("checkpoint_interval",256),checkpoint_budget=args.get("checkpoint_budget",16_000_000),
                recording_budget=args.get("recording_budget",32_000_000),
                file_access=FileAccess(args.get("read_root"),args.get("write_root")),compiled=compiled)
            timeline.source_project=compiled.source_project
            session=str(uuid4())
            if len(self.sessions)>=8:self.sessions.pop(next(iter(self.sessions)))
            self.sessions[session]=timeline
            return {"session":session,"image":base64.b64encode(image).decode(),"inspection":compiled.inspect(),
                    "source_project":timeline.source_project,**self.status(timeline)}
        if method == "restore":
            submitted_at=time.monotonic() if submitted_at is None else submitted_at
            document=args["document"]
            compiled=None
            if document.get("backend")=="selfhost":
                project=document.get("source_project")
                if not isinstance(project,dict):raise PixelError("temporal","Self-hosted replay requires its source snapshot")
                compiled=self.compiler.debug_project(project["files"],project.get("entry","main.pxl"),
                                                     cancelled=cancelled,submitted_at=submitted_at,include_png=True,scale=4)
            timeline=Timeline.restore(document,compiled=compiled)
            if compiled is not None:
                image=compiled.image
            else:
                buffer=io.BytesIO();encode_picture(timeline.source).save(buffer,format="PNG");image=buffer.getvalue()
            session=str(uuid4())
            if len(self.sessions)>=8:self.sessions.pop(next(iter(self.sessions)))
            self.sessions[session]=timeline
            return {"session":session,"project":timeline.source_project or project_text(timeline.source),
                    "source_project":timeline.source_project,"image":base64.b64encode(image).decode(),**self.status(timeline)}
        timeline = self.sessions.get(args.get("session"))
        if timeline is None:
            raise PixelError("studio", "Session expired; rebuild the project")
        if method == "inspect-ir":
            if timeline.compiled.ir is None:
                project=timeline.source_project
                if not project:raise PixelError("studio","IR source snapshot is unavailable")
                result=self.compiler.request("inspect-ir",files=project["files"],entry=project["entry"],
                                             cancelled=cancelled,submitted_at=submitted_at)["result"]
                timeline.compiled.ir=result["ir"]
            return {"ir":timeline.compiled.ir}
        if method == "breakpoints":
            executable = set()
            for function in timeline.compiled.bytecode["functions"].values():
                for instruction in function["instructions"]:
                    span = instruction.get("span", {}).get("text")
                    if span and span["source"] == args.get("source"):
                        executable.add(span["start"]["line"])
            return {
                "breakpoints": [
                    {
                        "line": line,
                        "verified": type(line) is int and line in executable,
                        **(
                            {}
                            if type(line) is int and line in executable
                            else {"message": "No executable instruction on this line"}
                        ),
                    }
                    for line in args.get("lines", [])
                ]
            }
        if method == "variables":
            return inspect_values(timeline, args)
        if method == "close":
            del self.sessions[args["session"]]
            return {}
        if method == "recording":
            return timeline.document()
        if method == "seek":
            timeline.seek(args["z"])
        elif method == "cancel":
            timeline.cancel()
        elif method in ("step", "continue", "run"):
            count = 1 if method == "step" else 2000
            breaks = args.get("breakpoints", {})
            initial_state = timeline.seek(timeline.cursor)
            initial = self.location(timeline, initial_state)
            initial_key = (
                (initial["source"], initial["start"]["line"]) if initial else None
            )
            left_initial = False
            for index in range(count):
                if timeline.cursor < len(timeline.events):
                    timeline.seek(timeline.cursor + 1)
                elif timeline.advance() is None:
                    break
                state = (
                    {
                        "function": timeline.vm.call_stack[-1].function
                        if timeline.vm.call_stack
                        else None,
                        "pc": timeline.vm.pc,
                        "halted": timeline.vm.halted,
                    }
                    if timeline.cursor == len(timeline.events)
                    else timeline.seek(timeline.cursor)
                )
                point = self.location(timeline, state)
                key = (point["source"], point["start"]["line"]) if point else None
                left_initial = left_initial or key != initial_key
                if (
                    state["halted"]
                    or method == "continue"
                    and point
                    and (
                        left_initial
                        or (state["function"], state["pc"])
                        == (initial_state["function"], initial_state["pc"])
                    )
                    and self.line_start(timeline, state, point)
                    and point["start"]["line"] in breaks.get(point["source"], [])
                ):
                    break
        elif method != "status":
            raise PixelError("studio", "Unknown service method")
        return self.status(timeline)

    def location(self, timeline, state):
        function = state["function"]
        if function is None:
            return None
        instructions = timeline.compiled.bytecode["functions"][function]["instructions"]
        pc = state["pc"]
        if not 0 <= pc < len(instructions):
            return None
        return instructions[pc].get("span", {}).get("text")

    def line_start(self, timeline, state, point=None):
        point = point or self.location(timeline, state)
        if not point:
            return False
        instructions = timeline.compiled.bytecode["functions"][state["function"]][
            "instructions"
        ]
        for pc, instruction in enumerate(instructions):
            text = instruction.get("span", {}).get("text")
            if text and (text["source"], text["start"]["line"]) == (
                point["source"],
                point["start"]["line"],
            ):
                return pc == state["pc"]
        return False

    def status(self, timeline):
        state = timeline.seek(timeline.cursor)
        start = max(1, timeline.cursor - 149)
        end = min(len(timeline.events), max(timeline.cursor, 1) + 50)
        frames = []
        for frame in reversed(state["call_stack"]):
            function = timeline.compiled.bytecode["functions"][frame["function"]]
            pc = min(frame["pc"], len(function["instructions"]) - 1)
            debug=getattr(timeline.compiled,"debug",None)
            if debug:
                info=debug["functions"][frame["function"]]
                arguments=[display_type(timeline,t) for t in info["typeArguments"]]
                frames.append({**frame,"display_name":info["name"]+("["+", ".join(arguments)+"]" if arguments else ""),
                    "names":[info["names"][i] if i<len(info["names"]) else f"slot {i}" for i in range(len(frame["memory"]))],
                    "location":function["instructions"][pc].get("span",{}).get("text"),
                    "values":[display(v) for v in frame["memory"]]})
                continue
            mid = frame["function"].split(":", 1)[0]
            node = timeline.compiled.modules["main"].data["_function_instances"][
                frame["function"]
            ]
            docs = {
                "main": timeline.source,
                **timeline.source.get("bundle", {}).get("modules", {}),
            }
            symbols = docs[mid].get("metadata", {}).get("symbols", {})

            def own_walk(statement):
                if statement.kind == "lambda":
                    return
                yield statement
                for key, value in statement.data.items():
                    if key.startswith("_"):
                        continue
                    if isinstance(value, Node):
                        yield from own_walk(value)
                    elif isinstance(value, list):
                        for child in value:
                            if isinstance(child, Node):
                                yield from own_walk(child)

            names = {
                n.data["_slot"]: symbols.get(str(n.data["name"]), str(n.data["name"]))
                for statement in node.data["body"]
                if statement.kind != "fn"
                for n in own_walk(statement)
                if n.kind in ("let", "for", "try")
            }
            names.update(
                {
                    n.data["_value_slot"]: symbols.get(
                        str(n.data["value_name"]), str(n.data["value_name"])
                    )
                    for statement in node.data["body"]
                    for n in own_walk(statement)
                    if n.kind == "for" and "value_name" in n.data
                }
            )
            names.update(
                {
                    p["_slot"]: symbols.get(str(p["name"]), str(p["name"]))
                    for p in node.data.get("params", [])
                }
            )
            names.update(
                {
                    p["_slot"]: symbols.get(str(p["name"]), str(p["name"]))
                    for statement in node.data["body"]
                    for n in own_walk(statement)
                    if n.kind == "case"
                    for p in n.data["params"]
                }
            )
            names.update(
                {
                    len(node.data["params"]) + i: symbols.get(str(name), str(name))
                    for i, name in enumerate(node.data.get("_capture_names", []))
                }
            )
            frames.append(
                {
                    **frame,
                    "display_name": symbols.get(
                        str(node.data.get("name", "lambda")),
                        str(node.data.get("name", "lambda")),
                    )
                    + (
                        "["
                        + ", ".join(
                            display_type(timeline, t)
                            for t in node.data["_type_arguments"]
                        )
                        + "]"
                        if node.data.get("_type_arguments")
                        else ""
                    ),
                    "names": [
                        names.get(i, f"slot {i}") for i in range(len(frame["memory"]))
                    ],
                    "location": function["instructions"][pc]
                    .get("span", {})
                    .get("text"),
                    "values": [display(v) for v in frame["memory"]],
                }
            )
        return {
            "cursor": timeline.cursor,
            "length": len(timeline.events),
            "recording": {
                "journal_bytes": timeline.events.bytes,
                "journal_budget": timeline.events.budget,
                "checkpoint_bytes": timeline.checkpoint_bytes,
                "checkpoint_budget": timeline.checkpoint_budget,
                "checkpoints": len(timeline.checkpoints),
                "last_seek_replayed": timeline.last_seek_replayed,
                "file_effect_bytes": timeline.file_access.bytes,
                "file_effect_budget": timeline.file_access.budget,
            },
            "state": state,
            "state_text": json.dumps(state, ensure_ascii=False, indent=2),
            "heap_values": {
                str(k): json.dumps(v, ensure_ascii=False)
                for k, v in state["heap"].items()
            },
            "stack_values": [display(v) for v in state["stack"]],
            "output": [display(v) for v in state["output"]],
            "frames": frames,
            "location": self.location(timeline, state),
            "line_start": self.line_start(timeline, state),
            "event": timeline.events[timeline.cursor - 1] if timeline.cursor else None,
            "volume": timeline.volume(start, end),
        }


class WorkstationLane:
    def __init__(self, service):
        self.service=service
        self.lock=threading.Lock()

    def request(self, request, *, cancelled=lambda:False, submitted_at=None):
        deadline=(time.monotonic() if submitted_at is None else submitted_at)+120
        while not self.lock.acquire(timeout=.025):
            if cancelled():return failure(request,'cancelled','Studio request cancelled')
            if time.monotonic()>=deadline:return failure(request,'timeout','Studio queue deadline exceeded')
        try:
            if cancelled():return failure(request,'cancelled','Studio request cancelled')
            if time.monotonic()>=deadline:return failure(request,'timeout','Studio queue deadline exceeded')
            result=self.service.request(request.get('method',request.get('operation')),request.get('args',{}),cancelled=cancelled,submitted_at=submitted_at)
            if cancelled():return failure(request,'cancelled','Studio request cancelled')
            return dict(version=1,id=request.get('id'),revision=request.get('revision'),status='ok',result=result)
        except (PixelError,ValueError,OSError,KeyError,TypeError) as error:
            diagnostic=error if isinstance(error,PixelError) else PixelError('studio',str(error))
            return dict(version=1,id=request.get('id'),revision=request.get('revision'),status=getattr(error,'status','error'),error=diagnostic.to_dict())
        finally:self.lock.release()


def main():
    service=Workstation()
    shutdown=threading.Event()
    def interrupted(signum,frame):
        shutdown.set();raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    try:
        serve(WorkstationLane(service),sys.stdin.buffer,sys.stdout,shutdown)
    except KeyboardInterrupt:
        pass
    finally:service.close()


if __name__ == "__main__":
    main()
