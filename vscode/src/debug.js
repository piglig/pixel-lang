const path = require("node:path");
const fs = require("node:fs");
const realPath = filename => fs.existsSync(filename) ? fs.realpathSync(filename) : path.resolve(filename);
class PixelDebug {
  constructor(vscode, host) {
    this.vscode = vscode;
    this.host = host;
    this.emitter = new vscode.EventEmitter();
    this.onDidSendMessage = this.emitter.event;
    this.seq = 1;
    this.breakpoints = {};
    this.pendingBreakpoints = new Map();
    this.nextBreakpointId = 1;
    this.running = false;
    this.epoch = 0;
    this.outputCount = 0;
    this.variablePaths = new Map();
    this.nextVariableReference = 100000;
  }
  send(message) {
    this.emitter.fire({ seq: this.seq++, ...message });
  }
  event(event, body = {}) {
    this.send({ type: "event", event, body });
  }
  respond(request, body = {}) {
    this.send({
      type: "response",
      request_seq: request.seq,
      command: request.command,
      success: true,
      body,
    });
  }
  async handleMessage(request) {
    const a = request.arguments || {};
    try {
      switch (request.command) {
        case "initialize":
          this.respond(request, {
            supportsConfigurationDoneRequest: true,
            supportsStepBack: true,
            supportsTerminateRequest: true,
            supportsVariablePaging: true,
          });
          this.event("initialized");
          break;
        case "launch": {
          const epoch = ++this.epoch;
          this.started = false;
          this.result = undefined;
          this.launchAbort?.abort();
          const controller = new AbortController();
          this.launchAbort = controller;
          const project = await this.host.collect(
            this.vscode.Uri.file(a.program),
          );
          const result = await this.host.service().call("build", { ...project, input: a.input || "" }, { signal: controller.signal });
          const latest = await this.host.collect(this.vscode.Uri.file(a.program));
          if (epoch !== this.epoch || controller.signal.aborted || latest.entry !== project.entry ||
              Object.keys(latest.files).length !== Object.keys(project.files).length ||
              Object.entries(project.files).some(([name, text]) => latest.files[name] !== text))
            throw new Error("Source changed or debug launch was cancelled.");
          this.project = project;
          this.result = result;
          this.launchAbort = undefined;
          this.host.show();
          this.host.publish(this.result, this.project);
          this.stopOnEntry = a.stopOnEntry !== false;
          for (const key of Object.keys(this.breakpoints)) {
            if (path.isAbsolute(key)) {
              this.breakpoints[
                path.relative(realPath(this.project.root), realPath(key)).split(path.sep).join("/")
              ] = this.breakpoints[key];
              delete this.breakpoints[key];
            }
          }
          for (const [filename, pending] of this.pendingBreakpoints) {
            const verified = await this.validateBreakpoints(filename, pending);
            for (const breakpoint of verified) this.event("breakpoint", { reason: "changed", breakpoint });
          }
          this.pendingBreakpoints.clear();
          this.respond(request);
          this.startConfigured();
          break;
        }
        case "configurationDone":
          this.configured = true;
          this.respond(request);
          this.startConfigured();
          break;
        case "setBreakpoints": {
          const pending = (a.breakpoints || []).map(b => ({
            ...b, id: this.nextBreakpointId++,
          }));
          let breakpoints;
          if (this.result) breakpoints = await this.validateBreakpoints(a.source.path, pending);
          else {
            this.pendingBreakpoints.set(a.source.path, pending);
            breakpoints = pending.map(b => ({ id: b.id, line: b.line, verified: false,
              message: "Waiting for compiled program" }));
          }
          this.respond(request, { breakpoints });
          break;
        }
        case "threads":
          this.respond(request, { threads: [{ id: 1, name: "PixelVM" }] });
          break;
        case "stackTrace":
          this.respond(request, {
            stackFrames: await Promise.all((this.result?.frames || []).map(async (f, i) => ({
              id: i,
              name: f.display_name || f.function,
              line: f.location?.start.line || 1,
              column: f.location ? await this.sourceColumn(f.location) : 1,
              source: f.location
                ? {
                    name: f.location.source,
                    path: await this.sourcePath(f.location.source),
                  }
                : undefined,
            }))),
            totalFrames: this.result?.frames?.length || 0,
          });
          break;
        case "scopes":
          this.respond(request, {
            scopes: [
              {
                name: "Locals",
                variablesReference: 100 + a.frameId,
                expensive: false,
              },
              { name: "Heap", variablesReference: 1, expensive: false },
              {
                name: "Operand stack",
                variablesReference: 2,
                expensive: false,
              },
            ],
          });
          break;
        case "variables": {
          let variablePath;
          if (a.variablesReference >= 100000) {
            variablePath = this.variablePaths.get(a.variablesReference);
            if (!variablePath) throw new Error("Variable reference expired; expand the current scope.");
          } else if (a.variablesReference >= 100) {
            variablePath = ["frame", a.variablesReference - 100];
          } else variablePath = [a.variablesReference === 1 ? "heap" : "stack"];
          const page = await this.host.service().call("variables", {
            session: this.result.session, z: this.result.cursor,
            path: variablePath, start: a.start || 0, count: Math.min(a.count || 100, 1000),
          });
          const values = page.variables.map(value => {
            let reference = 0;
            if (value.path) {
              reference = this.nextVariableReference++;
              this.variablePaths.set(reference, value.path);
            }
            const frame = variablePath[0] === "frame" && variablePath.length === 2
              ? this.result.frames[variablePath[1]] : null;
            const { path: unusedPath, ...descriptor } = value;
            return { ...descriptor,
              name: frame?.names?.[Number(value.name)] || value.name,
              variablesReference: reference,
            };
          });
          this.respond(request, { variables: values });
          break;
        }
        case "continue":
          this.respond(request, { allThreadsContinued: true });
          this.resume();
          break;
        case "next":
        case "stepIn":
        case "stepOut":
          this.respond(request);
          this.move(request.command);
          break;
        case "stepBack":
          this.respond(request);
          this.running = false;
          this.epoch++;
          this.result = {
            ...this.result,
            ...(await this.host.service().call("seek", {
              session: this.result.session,
              z: Math.max(0, this.result.cursor - 1),
            })),
          };
          this.stopped("step");
          break;
        case "pause":
          this.running = false;
          this.epoch++;
          this.respond(request);
          this.stopped("pause");
          break;
        case "disconnect":
        case "terminate":
          this.launchAbort?.abort();
          this.running = false;
          this.epoch++;
          this.respond(request);
          this.event("terminated");
          break;
        case "setExceptionBreakpoints":
          this.respond(request);
          break;
        default:
          this.respond(request);
      }
    } catch (error) {
      this.send({
        type: "response",
        request_seq: request.seq,
        command: request.command,
        success: false,
        message: error.message,
      });
    }
  }
  async sourceColumn(location) {
    const doc = await this.host.sourceDocument(location.source, this.project);
    const line = doc.lineAt(Math.max(0, location.start.line - 1)).text;
    return Array.from(line).slice(0, location.start.column - 1).join("").length + 1;
  }
  async sourcePath(filename) {
    const doc = await this.host.sourceDocument(filename, this.project);
    return doc.uri.scheme === "file" ? doc.uri.fsPath : doc.uri.toString();
  }
  async validateBreakpoints(filename, pending) {
    const source = Object.keys(this.project.paths || {}).find(name => realPath(this.project.paths[name]) === realPath(filename))
      || path.relative(realPath(this.project.root), realPath(filename)).split(path.sep).join("/");
    const result = await this.host.service().call("breakpoints", {
      session: this.result.session, source, lines: pending.map(b => b.line),
    });
    const breakpoints = result.breakpoints.map((b, i) => {
      const requested = pending[i];
      if (requested.condition || requested.hitCondition || requested.logMessage)
        return { id: requested.id, line: requested.line, verified: false,
          message: "Conditional, hit-count and log breakpoints are not supported" };
      return { ...b, id: requested.id };
    });
    this.breakpoints[source] = breakpoints.filter(b => b.verified).map(b => b.line);
    return breakpoints;
  }
  point() {
    const p = this.result?.location;
    return p ? `${p.source}:${p.start.line}` : "";
  }
  startConfigured() {
    if (!this.configured || !this.result || this.started) return;
    this.started = true;
    if (this.stopOnEntry) this.stopped("entry");
    else this.resume();
  }
  stopped(reason) {
    this.variablePaths.clear();
    this.host.publish(this.result, this.project);
    this.host.reveal(this.result.location, this.project);
    this.event("stopped", {
      reason: this.result.state.error ? "exception" : reason,
      description: this.result.state.error?.message,
      threadId: 1,
      allThreadsStopped: true,
    });
  }
  output() {
    for (const text of this.result.output.slice(this.outputCount))
      this.event("output", { category: "stdout", output: text + "\n" });
    this.outputCount = this.result.output.length;
  }
  async move(kind) {
    try {
      this.running = false;
      const epoch = ++this.epoch,
        start = this.point(),
        depth = this.result.frames.length;
      for (let i = 0; i < 100_000 && epoch === this.epoch; i++) {
        this.result = {
          ...this.result,
          ...(await this.host
            .service()
            .call("step", { session: this.result.session })),
        };
        if (this.result.state.halted) break;
        const nextDepth = this.result.frames.length;
        if (
          kind === "stepOut"
            ? nextDepth < depth
            : this.point() !== start &&
              (kind === "stepIn" || nextDepth <= depth)
        )
          break;
      }
      this.output();
      this.stopped("step");
    } catch (e) {
      this.event("output", { category: "stderr", output: e.message + "\n" });
      this.stopped("exception");
    }
  }
  async resume() {
    this.running = true;
    const epoch = ++this.epoch;
    try {
      while (this.running && epoch === this.epoch) {
        this.result = {
          ...this.result,
          ...(await this.host.service().call("continue", {
            session: this.result.session,
            breakpoints: this.breakpoints,
          })),
        };
        this.output();
        const p = this.result.location;
        if (
          this.result.state.halted ||
          (p &&
            this.result.line_start &&
            this.breakpoints[p.source]?.includes(p.start.line))
        )
          break;
        await new Promise((r) => setTimeout(r, 0));
      }
      if (epoch === this.epoch) {
        this.running = false;
        this.stopped(this.result.state.halted ? "pause" : "breakpoint");
      }
    } catch (e) {
      this.event("output", { category: "stderr", output: e.message + "\n" });
      this.stopped("exception");
    }
  }
  dispose() {
    this.launchAbort?.abort();
    this.running = false;
    this.epoch++;
    this.emitter.dispose();
  }
}
module.exports = { PixelDebug };
