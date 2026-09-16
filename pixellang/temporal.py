"""Deterministic three-dimensional execution: X/Y source, Z logical time.

Events retain only instruction provenance. Exact historical VM state is reconstructed
on demand, avoiding a heap-sized copy at every instruction.
"""

from copy import deepcopy
import hashlib
from .bootstrap import normalized

from .compiler import compile_source
from .fileaccess import FileAccess
from .model import PixelError
from .recording import InstructionJournal, footprint
from .vm import VM


class Timeline:
    def __init__(
        self,
        source,
        input_text="",
        max_steps=100_000,
        checkpoint_interval=256,
        checkpoint_budget=16_000_000,
        recording_budget=32_000_000,
        file_access=None,
        compiled=None,
    ):
        self.source_project = None
        if type(max_steps) is not int or not 1 <= max_steps <= 1_000_000:
            raise PixelError("temporal", "Time recording budget must be 1..1000000")
        if not isinstance(source, dict):
            raise PixelError("temporal", "Timeline requires a spatial source document")
        self.source = deepcopy(source)
        self.compiled = compiled if compiled is not None else compile_source(self.source)
        if compiled is not None and compiled.source != self.source:
            raise PixelError("temporal", "Compiled debug source differs from timeline source")
        self.input_text = input_text
        self.max_steps = max_steps
        self.file_access = file_access if file_access is not None else FileAccess()
        self.vm = self.fresh_vm(live=True)
        if (
            type(checkpoint_interval) is not int
            or not 1 <= checkpoint_interval <= 65536
        ):
            raise PixelError("temporal", "Invalid checkpoint interval")
        if any(
            type(v) is not int or not 1024 <= v <= 128_000_000
            for v in (checkpoint_budget, recording_budget)
        ):
            raise PixelError("temporal", "Invalid recording/checkpoint budget")
        self.events = InstructionJournal(self.compiled.bytecode, recording_budget)
        self.checkpoint_interval = checkpoint_interval
        self.checkpoint_budget = checkpoint_budget
        self.checkpoints = {}
        self.checkpoint_bytes = 0
        self.last_seek_replayed = 0
        self.cancelled_at = None
        self._save_checkpoint(0)
        self.cursor = 0
        self.replay_vm = None
        self.replay_z = 0

    def volume(self, start=1, end=None):
        """Sparse XYZ geometry; each module retains its own X/Y coordinate plane."""
        end = len(self.events) if end is None else end
        if (
            type(start) is not int
            or type(end) is not int
            or not 1 <= start <= end + 1 <= len(self.events) + 1
        ):
            raise PixelError("temporal", "Invalid volume time range")
        documents = {
            "main": self.source,
            **self.source.get("bundle", {}).get("modules", {}),
        }
        colors = {
            mid: {tuple(p["position"]): p["rgba"] for p in doc["pixels"]}
            for mid, doc in documents.items()
        }
        voxels = []
        for event in self.events[start - 1 : end]:
            debug = getattr(self.compiled,"debug",None)
            module = debug["functions"][event["function"]]["module"] if debug else event["function"].split(":", 1)[0]
            for x, y, z in event["points"]:
                pixel = colors[module].get((x, y))
                if pixel and pixel[3]:
                    voxels.append(
                        {"position": [x, y, z], "rgba": pixel, "module": module}
                    )
        return {"axes": ["x", "y", "time"], "range": [start, end], "voxels": voxels}

    def fresh_vm(self, live=False):
        return VM(
            self.compiled.bytecode,
            input_text=self.input_text,
            max_steps=self.max_steps,
            file_access=self.file_access if live else self.file_access.replay(),
        )

    def advance(self):
        if self.vm.halted:
            return None
        frame = self.vm.call_stack[-1]
        function, pc = frame.function, frame.pc
        self._reserve_transition(function, pc, self.vm.cancelled)
        try:
            self.vm.step(snapshot=False)
        except PixelError:
            pass
        self.events.append(function, pc, self.vm.steps, self.vm.error)
        self.cursor = len(self.events)
        if self.cursor % self.checkpoint_interval == 0 or self.vm.halted:
            self._save_checkpoint(self.cursor)
        return self.events[-1]

    def _reserve_transition(self, function, pc, cancellation=False):
        # Reserve journal space before mutating the VM. Includes room for a bounded error.
        if cancellation:
            required = self.events.record_cost(
                self.events.make_record(
                    function,
                    pc,
                    self.vm.steps,
                    {"phase": "runtime", "message": "Execution cancelled", "span": {}},
                )[3]
            )
        else:
            required = self.events.record_cost() + 4096
        if self.events.bytes + required > self.events.budget:
            raise PixelError(
                "temporal",
                "Recording budget exceeded; execution is paused before the next instruction",
            )

    def cancel(self):
        """Cancel the live execution, retaining its earlier inspectable history."""
        if not self.vm.halted:
            frame = self.vm.call_stack[-1]
            self._reserve_transition(frame.function, frame.pc, cancellation=True)
            self.cancelled_at = len(self.events)
            self.vm.cancel()
            self.advance()

    def _save_checkpoint(self, z):
        snapshot = self.vm.checkpoint()
        size = footprint(snapshot)
        if size > self.checkpoint_budget:
            return
        if z in self.checkpoints:
            self.checkpoint_bytes -= self.checkpoints.pop(z)[1]
        self.checkpoints[z] = (snapshot, size)
        self.checkpoint_bytes += size
        while self.checkpoint_bytes > self.checkpoint_budget:
            keys = sorted(self.checkpoints)
            if len(keys) <= 2:
                # Time zero can always be reconstructed by a fresh VM. Prefer a
                # useful recent snapshot when the budget cannot hold both ends.
                victim = keys[0]
            else:
                # Preserve both ends and remove the cheapest interior coverage
                # per byte. FIFO eviction makes old seeks replay from time zero;
                # thinning dense intervals keeps the full history accessible.
                victim = min(
                    range(1, len(keys) - 1),
                    key=lambda i: (keys[i + 1] - keys[i - 1])
                    / self.checkpoints[keys[i]][1],
                )
                victim = keys[victim]
            self.checkpoint_bytes -= self.checkpoints.pop(victim)[1]

    def run(self):
        while not self.vm.halted:
            self.advance()
        return self.vm.state()

    def seek(self, z):
        if type(z) is not int or not 0 <= z <= len(self.events):
            raise PixelError("temporal", "Time position is outside the recorded run")
        self.cursor = z
        self.last_seek_replayed = 0
        if z == len(self.events):
            return self.vm.state()
        checkpoint_z = max((key for key in self.checkpoints if key <= z), default=-1)
        if self.replay_vm is None or z < self.replay_z or checkpoint_z > self.replay_z:
            self.replay_vm = self.replay_vm or self.fresh_vm()
            if checkpoint_z >= 0:
                self.replay_vm.restore_checkpoint(self.checkpoints[checkpoint_z][0])
                self.replay_z = checkpoint_z
            else:
                self.replay_vm = self.fresh_vm()
                self.replay_z = 0
        while self.replay_z < z:
            try:
                self.replay_vm.step(snapshot=False)
            except PixelError:
                pass
            self.replay_z += 1
            self.last_seek_replayed += 1
        return self.replay_vm.state()

    def document(self):
        return {
            **({"backend":"selfhost","compiled_sha256":hashlib.sha256(normalized(self.compiled.bytecode).encode()).hexdigest()} if getattr(self.compiled,"debug",None) else {}),
            "format": "pixellang-time",
            "version": "0.8",
            "axes": {
                "x": "source-column",
                "y": "source-row",
                "z": "logical-transition",
            },
            "source": deepcopy(self.source),
            "source_project": deepcopy(self.source_project),
            "input": self.input_text,
            "max_steps": self.max_steps,
            "length": len(self.events),
            "cursor": self.cursor,
            "cancelled_at": self.cancelled_at,
            "files": self.file_access.document(),
            "recording": {
                "checkpoint_interval": self.checkpoint_interval,
                "checkpoint_budget": self.checkpoint_budget,
                "recording_budget": self.events.budget,
            },
        }

    @classmethod
    def restore(cls, document, compiled=None):
        if (
            not isinstance(document, dict)
            or document.get("format") != "pixellang-time"
            or document.get("version") != "0.8"
        ):
            raise PixelError("temporal", "Unsupported timeline document")
        length, cursor, limit = (
            document.get(k) for k in ("length", "cursor", "max_steps")
        )
        if (
            any(type(v) is not int for v in (length, cursor, limit))
            or not 1 <= limit <= 1_000_000
            or not 0 <= cursor <= length <= limit + 1
        ):
            raise PixelError("temporal", "Invalid timeline bounds")
        options = document.get("recording", {})
        if not isinstance(options, dict) or set(options) - {
            "checkpoint_interval",
            "checkpoint_budget",
            "recording_budget",
        }:
            raise PixelError("temporal", "Invalid recording configuration")
        cancelled_at = document.get("cancelled_at")
        if cancelled_at is not None and (
            type(cancelled_at) is not int
            or cancelled_at < 0
            or cancelled_at != length - 1
        ):
            raise PixelError("temporal", "Invalid cancellation position")
        native=document.get('backend')=='selfhost'
        if document.get('backend') not in (None,'selfhost'):
            raise PixelError('temporal','Unsupported recorded compiler backend')
        if native:
            if compiled is None:
                project=document.get('source_project')
                if not isinstance(project,dict):raise PixelError('temporal','Self-hosted replay requires its source snapshot')
                from .compiler_client import CompilerClient
                with CompilerClient() as compiler:
                    compiled=compiler.debug_project(project['files'],project.get('entry','main.pxl'))
            if not getattr(compiled,'debug',None) or hashlib.sha256(normalized(compiled.bytecode).encode()).hexdigest()!=document.get('compiled_sha256'):
                raise PixelError('temporal','Recompiled program differs from recorded bytecode; source snapshot or compiler changed')
        elif compiled is not None:
            raise PixelError('temporal','Cannot override compiler for a legacy recording')
        timeline = cls(
            document.get("source"),
            document.get("input", ""),
            limit,
            file_access=FileAccess.restore(
                document.get("files", {"budget": 8_000_000, "effects": []})
            ),
            compiled=compiled,
            **options,
        )
        source_project = document.get("source_project")
        if source_project is not None:
            if native:
                matches=isinstance(source_project,dict) and compiled.source_project==source_project
            else:
                from .project import build_project
                matches=isinstance(source_project,dict) and build_project(source_project.get('files'),source_project.get('entry','main.pxl'))==timeline.source
            if not matches:
                raise PixelError('temporal','Source snapshot does not match recorded program')
            timeline.source_project = deepcopy(source_project)
        for z in range(length):
            if z == cancelled_at:
                if timeline.vm.halted:
                    raise PixelError("temporal", "Cancellation follows termination")
                timeline.cancel()
                continue
            if timeline.advance() is None:
                raise PixelError(
                    "temporal", "Timeline extends past program termination"
                )
        if timeline.vm.effect_cursor != len(timeline.file_access.effects):
            raise PixelError(
                "temporal", "File effect count does not match recorded execution"
            )
        timeline.seek(cursor)
        return timeline
