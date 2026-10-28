"""Local JSON-lines service for editor hosts. No HTTP server or host-code execution."""

import base64
import argparse
import json
import sys
import signal
import sqlite3
import threading
import time
import weakref
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4


from .compiler_client import CompilerClient
from .compiler_service import Limits
from .compiler_rpc import serve
from .compiler_worker import failure
from .cli import display
from .debugvalues import display_type, inspect_values
from .flow_debug import flow_scopes
from .native_editor import NativeSymbolIndex
from .fileaccess import FileAccess
from .model import PixelError
from .temporal import Timeline
from .workspace import Workspace


def close_applications(compiler, applications, editor_compiler=None):
    try:
        for application in applications.values():
            application.close()
        applications.clear()
    finally:
        try:
            compiler.close()
        finally:
            if editor_compiler is not None:
                editor_compiler.close()


class Workstation:
    def __init__(self, *, compiler_artifact=None, native_executable=None):
        self.sessions = {}
        self.application_sessions = {}
        # Explicit builds must accommodate the self-hosted compiler itself.
        # Editor analysis retains the smaller default budget. Wall time and
        # memory bounds are identical on both independently cancellable lanes.
        self.compiler = CompilerClient(compiler_artifact=compiler_artifact,native_executable=native_executable,
                                       limits=Limits(steps=3_000_000_000))
        self.editor_compiler = CompilerClient(compiler_artifact=compiler_artifact,native_executable=native_executable)
        self.editor_snapshot = None
        self.editor_index = None
        self.build_cache = None
        self._finalizer = weakref.finalize(self, close_applications, self.compiler, self.application_sessions, self.editor_compiler)

    def close(self):
        self.build_cache = None
        self._finalizer()

    def symbol_index(self, files, entry, *, roots=None, cancelled=lambda: False, submitted_at=None, revision=None):
        sources = self.editor_compiler.sources(files)
        roots = tuple(sorted(files if roots is None else roots))
        scope = (entry, roots, tuple(sorted(sources)), tuple(sorted(files)))
        if cancelled():
            raise PixelError('service', 'Editor request cancelled', code='service.cancelled')
        previous = self.editor_index
        if previous is not None and self.editor_snapshot == scope:
            # Only compiler-resolved dependencies can invalidate this analysis.
            # The source-name set above also detects newly supplied missing imports.
            dependencies = set(previous.paths) | set(roots) | {entry}
            if all(previous.files.get(name) == sources.get(name) for name in dependencies):
                if previous.files != sources:
                    previous = NativeSymbolIndex(sources, entry, previous.analysis, editable_files=files)
                    self.editor_index = previous
                return previous
        analysis = self.editor_compiler.request('editor-index', files=sources, entry=entry, editorRoots=list(roots),
            cancelled=cancelled, submitted_at=submitted_at, revision=revision)['result']['index']
        index = NativeSymbolIndex(sources, entry, analysis, editable_files=files)
        if sum(len(text) for text in sources.values()) <= 2_000_000:
            self.editor_snapshot, self.editor_index = scope, index
        else:
            self.editor_snapshot = self.editor_index = None
        return index

    def request(self, method, args, *, cancelled=lambda:False, submitted_at=None):
        if method.startswith('application-'):
            from .application_session import ApplicationSession
            if method == 'application-open':
                application = ApplicationSession(self.compiler, args.get('path'), files=args.get('files'),
                    entry=args.get('entry', 'main.pxl'), cancelled=cancelled, journal_path=args.get('journalPath'),
                    configuration=args.get('configuration'))
                try:
                    status = application.status(include_source=True)
                except BaseException:
                    application.close()
                    raise
                session = str(uuid4())
                if len(self.application_sessions) >= 4:
                    self.application_sessions.pop(next(iter(self.application_sessions))).close()
                self.application_sessions[session] = application
                return dict(session=session, **status)
            application = self.application_sessions.get(args.get('session'))
            if application is None:
                raise PixelError('event', 'Application session expired; reopen the program')
            if method == 'application-debug':
                timeline = application.debug_event(args['index'])
                session = str(uuid4())
                if len(self.sessions) >= 8:
                    self.sessions.pop(next(iter(self.sessions)))
                self.sessions[session] = timeline
                return dict(session=session, source_project=timeline.source_project, **self.status(timeline))
            if method == 'application-close':
                application.close()
                del self.application_sessions[args['session']]
                return {}
            return dict(session=args['session'], **application.request(method, args))
        if method == 'native-graphs':
            return self.compiler.request('native-graphs', files=args['files'], entry=args.get('entry', 'main.pxl'),
                                         cancelled=cancelled, submitted_at=submitted_at)['result']
        if method == "check":
            checked=self.editor_compiler.request("check",files=args["files"],entry=args.get("entry","main.pxl"),
                                          cancelled=cancelled,revision=args.get("revision"),submitted_at=submitted_at)
            return {"ok":True,"metrics":checked["metrics"],"compilerSha256":checked["compilerSha256"]}

        if method in ("spatial-inspect", "spatial-edit"):
            from .spatialedit import SpatialProject
            project = SpatialProject(args["files"], args.get("entry", "main.pxl"), compiler=self.compiler,
                                     cancelled=cancelled, submitted_at=submitted_at)
            return (
                project.describe(args["source"])
                if method == "spatial-inspect"
                else project.edit(args)
            )
        if method in ("project-test", "project-build", "project-lock"):
            workspace = Workspace(args["root"])
            if method == "project-test":
                return workspace.test(args.get("max_steps", 100_000),
                                      pattern=args.get("filter"), list_only=args.get("list", False),
                                      test_id=args.get("test_id"))
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

        if method == 'format':
            submitted_at = time.monotonic() if submitted_at is None else submitted_at
            files = args['files']
            formatted = {}
            for name, text in files.items():
                formatted[name] = self.editor_compiler.request('format', files={name: text}, entry=name,
                    cancelled=cancelled, submitted_at=submitted_at,
                    revision=args.get('revision'))['result']['text']
            return dict(entry=args.get('entry', 'main.pxl'), files=formatted)

        if method in ('completion', 'signature-help', 'definition', 'references', 'rename', 'hover', 'document-symbols'):
            submitted_at = time.monotonic() if submitted_at is None else submitted_at
            entry = args.get('entry', 'main.pxl')
            files = args['files']
            whole_project = method in ('references', 'rename')
            roots = sorted(files) if whole_project else [args['source']]
            analysis_entry = entry if whole_project else args['source']
            def analyze(snapshot):
                return self.symbol_index({name: text for name, text in snapshot.items() if name in files}, analysis_entry, roots=roots,
                    cancelled=cancelled, submitted_at=submitted_at, revision=args.get('revision'))
            index = analyze(files)
            if method == 'completion':
                return index.complete(args['source'], args['offset'], analyze=analyze)
            if method == 'signature-help':
                return index.signature_help(args['source'], args['offset'])
            if method == 'rename':
                return index.rename(args['source'], args['offset'], args['new_name'], analyze=analyze)
            if method == 'hover':
                return index.hover(args['source'], args['offset'])
            if method == 'document-symbols':
                return index.document_symbols(args['source'])
            result = (index.definition(args['source'], args['offset']) if method == 'definition'
                      else index.references(args['source'], args['offset'], args.get('include_declaration', True)))
            if args.get('with_sources'):
                return dict(result=result, files=index.project['sources'])
            return result
        if method == "recover":
            image = base64.b64decode(args["image"], validate=True)
            return self.compiler.request('recover-png',image=image,cancelled=cancelled,
                                         submitted_at=submitted_at)['result']
        if method == "build":
            if args.get("lock_error"):raise PixelError("project",args["lock_error"])
            submitted_at=time.monotonic() if submitted_at is None else submitted_at
            entry=args.get("entry","main.pxl")
            compile_started=time.monotonic()
            if cancelled():raise PixelError('service','Build cancelled',code='service.cancelled')
            seconds=getattr(getattr(self.compiler.worker,'limits',None),'seconds',120)
            if compile_started>=submitted_at+seconds:
                raise PixelError('service','Build deadline exceeded',code='service.timeout')
            snapshot=self.compiler.sources(args['files'])
            key=(self.compiler,entry,tuple(sorted(snapshot.items())))
            cache_hit=self.build_cache is not None and self.build_cache[0]==key
            if cache_hit:
                _,template,code_image=self.build_cache
                compiled=deepcopy(template)
            else:
                compiled=self.compiler.debug_project(snapshot,entry,cancelled=cancelled,submitted_at=submitted_at,
                                                     include_ir=False)
                code_image=None
            compiled_at=time.monotonic()
            timeline=Timeline(compiled.source,args.get("input",""),args.get("max_steps",100_000),
                checkpoint_interval=args.get("checkpoint_interval",256),checkpoint_budget=args.get("checkpoint_budget",16_000_000),
                recording_budget=args.get("recording_budget",32_000_000),
                file_access=FileAccess(args.get("read_root"),args.get("write_root")),compiled=compiled,
                arguments=args.get("arguments"),code_image=code_image)
            if cancelled():raise PixelError('service','Build cancelled',code='service.cancelled')
            if time.monotonic()>=submitted_at+seconds:
                raise PixelError('service','Build deadline exceeded',code='service.timeout')
            if not cache_hit:
                # Retain one successful snapshot. Only immutable execution data
                # is shared; debug metadata and each session's state stay private.
                template=deepcopy(replace(compiled,bytecode=timeline.code_image.data))
                self.build_cache=(key,template,timeline.code_image)
            session=str(uuid4())
            if len(self.sessions)>=8:self.sessions.pop(next(iter(self.sessions)))
            self.sessions[session]=timeline
            state=self.status(timeline)
            finished=time.monotonic()
            return {"session":session,"inspection":{"ir":None},
                    "buildCacheHit":cache_hit,
                    "timings":{"queueMs":(compile_started-submitted_at)*1000,
                               "compileMs":(compiled_at-compile_started)*1000,
                               "debugInitMs":(finished-compiled_at)*1000},
                    "source_project":timeline.source_project,**state}
        if method == "restore":
            submitted_at=time.monotonic() if submitted_at is None else submitted_at
            document=args["document"]
            if not isinstance(document,dict) or document.get("backend")!="selfhost":
                raise PixelError("temporal","Studio replay requires a native compiler recording")
            project=document.get("source_project")
            if not isinstance(project,dict) or not isinstance(project.get("files"),dict):
                raise PixelError("temporal","Native replay requires its source snapshot")
            compiled=self.compiler.debug_project(project["files"],project.get("entry","main.pxl"),
                                                 cancelled=cancelled,submitted_at=submitted_at)
            timeline=Timeline.restore(document,compiled=compiled)
            session=str(uuid4())
            if len(self.sessions)>=8:self.sessions.pop(next(iter(self.sessions)))
            self.sessions[session]=timeline
            return {"session":session,"project":timeline.source_project,
                    "source_project":timeline.source_project,**self.status(timeline)}
        timeline = self.sessions.get(args.get("session"))
        if timeline is None:
            raise PixelError("studio", "Session expired; rebuild the project")
        if method == "inspect-artifact":
            return timeline.compiled.inspect()
        if method == "inspect-projection":
            if timeline.source is None:
                project=timeline.source_project
                result=self.compiler.request("debug-pixels",files=project["files"],entry=project["entry"],
                                             cancelled=cancelled,submitted_at=submitted_at)["result"]
                timeline.set_projection(result["document"])
            return self.status(timeline)
        if method == "inspect-image":
            if timeline.compiled.image is None:
                project=timeline.source_project
                result=self.compiler.request("export-debug-png",files=project["files"],entry=project["entry"],
                                             scale=8,cancelled=cancelled,submitted_at=submitted_at)["result"]
                timeline.compiled.image=result["image"]
            return {"image":base64.b64encode(timeline.compiled.image).decode()}
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
        if method == "native-space-view":
            from .spatial_debug_view import space_view
            return space_view(timeline, args)
        if method == "flow-frames":
            from .flow_debug_view import frame_view
            return frame_view(timeline, args)
        if method == "flow-graph":
            from .flow_debug_view import graph_view
            return graph_view(timeline, args)
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
            visited = {(initial_state["function"], initial_state["pc"])}
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
                site = (state["function"], state["pc"])
                revisited = site in visited
                visited.add(site)
                if (
                    state["halted"]
                    or method == "continue"
                    and point
                    and (
                        left_initial
                        or revisited
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
        # A loop header can follow initialization on the same source line.
        # Returning to it is a new breakpoint visit even when the line's first
        # instruction executes only once.
        if any(instruction["op"] == "JUMP" and instruction.get("arg") == state["pc"]
               and pc >= state["pc"] for pc, instruction in enumerate(instructions)):
            return True
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
            "flow_scopes": flow_scopes(timeline.vm if timeline.cursor == len(timeline.events) else timeline.replay_vm),
            "location": self.location(timeline, state),
            "line_start": self.line_start(timeline, state),
            "event": timeline.projected_event(timeline.events[timeline.cursor - 1]) if timeline.cursor else None,
            "projection_ready": timeline.source is not None,
            "volume": timeline.volume(start, end),
        }


EDITOR_METHODS = frozenset({
    "check", "format", "completion", "signature-help", "definition",
    "references", "rename", "hover", "document-symbols",
})


class WorkstationLane:
    def __init__(self, service):
        self.service=service
        self.lock=threading.Lock()
        self.project_lock=threading.Lock()
        self.editor_lock=threading.Lock()

    def request(self, request, *, cancelled=lambda:False, submitted_at=None):
        deadline=(time.monotonic() if submitted_at is None else submitted_at)+120
        method=request.get('method',request.get('operation'))
        # Project jobs own their compiler and VM. They must not monopolize the
        # editor/session lane. Workspace reads touch no shared compiler state.
        lock=(None if method=='workspace' else self.project_lock
              if method in ('project-test','project-build','project-lock') else self.editor_lock if method in EDITOR_METHODS else self.lock)
        while lock is not None and not lock.acquire(timeout=.025):
            if cancelled():return failure(request,'cancelled','Studio request cancelled')
            if time.monotonic()>=deadline:return failure(request,'timeout','Studio queue deadline exceeded')
        try:
            if cancelled():return failure(request,'cancelled','Studio request cancelled')
            if time.monotonic()>=deadline:return failure(request,'timeout','Studio queue deadline exceeded')
            result=self.service.request(request.get('method',request.get('operation')),request.get('args',{}),cancelled=cancelled,submitted_at=submitted_at)
            if cancelled():return failure(request,'cancelled','Studio request cancelled')
            return dict(version=1,id=request.get('id'),revision=request.get('revision'),status='ok',result=result)
        except (PixelError,ValueError,OSError,KeyError,TypeError,sqlite3.Error) as error:
            diagnostic=error if isinstance(error,PixelError) else PixelError('studio',str(error))
            return dict(version=1,id=request.get('id'),revision=request.get('revision'),status=getattr(error,'status','error'),error=diagnostic.to_dict())
        finally:
            if lock is not None:lock.release()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler-artifact',type=Path)
    parser.add_argument('--native-compiler',type=Path)
    args=parser.parse_args()
    if args.native_compiler is not None and args.compiler_artifact is None:
        parser.error('--native-compiler requires its matching --compiler-artifact')
    service=Workstation(compiler_artifact=args.compiler_artifact,native_executable=args.native_compiler)
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
