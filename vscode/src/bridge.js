const { spawn } = require("node:child_process");
const readline = require("node:readline");
const { performance } = require("node:perf_hooks");
const editorMethods = new Set(['check', 'format', 'completion', 'signature-help',
  'definition', 'references', 'rename', 'hover', 'document-symbols']);
function encodeWire(value) {
  if (Buffer.isBuffer(value)) return { $bytes: value.toString("base64") };
  if (Array.isArray(value)) return value.map(encodeWire);
  if (value && typeof value === "object") {
    const entries = Object.entries(value).map(([k, v]) => [k, encodeWire(v)]);
    if (Object.hasOwn(value, "$bytes") || Object.hasOwn(value, "$escaped")) return { $escaped: entries };
    return Object.fromEntries(entries);
  }
  return value;
}
function decodeWire(value) {
  if (Array.isArray(value)) return value.map(decodeWire);
  if (value && typeof value === "object") {
    const keys = Object.keys(value);
    if (keys.length === 1 && keys[0] === "$bytes") return Buffer.from(value.$bytes, "base64");
    if (keys.length === 1 && keys[0] === "$escaped") return Object.fromEntries(value.$escaped.map(([k, v]) => [k, decodeWire(v)]));
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, decodeWire(v)]));
  }
  return value;
}
class Bridge {
  constructor(python, runtime, log) {
    this.pending = new Map();
    this.active = 0;
    this.next = 0;
    this.process = spawn(python, ["-u", "-m", "pixellang.workstation"], {
      cwd: runtime,
      env: { ...process.env, PYTHONPATH: runtime, PYTHONIOENCODING: "utf-8" },
      stdio: ["pipe", "pipe", "pipe"],
    });
    readline
      .createInterface({ input: this.process.stdout })
      .on("line", (line) => {
        try {
          const m = decodeWire(JSON.parse(line)),
            p = this.pending.get(m.id);
          if (p) {
            this.pending.delete(m.id);
            if (p.sent) this.active--;
            p.cleanup?.();
            const elapsed = Math.round(performance.now() - p.started);
            const queued = Math.round((p.sentAt ?? p.started) - p.started);
            log.appendLine(`Studio ${p.method}: ${elapsed} ms (${queued} ms in transport queue), ${m.error ? "error" : "ok"}`);
            if (p.method === 'build' && m.result?.timings) {
              const t = m.result.timings;
              log.appendLine(`Build stages: service queue ${Math.round(t.queueMs)} ms, compile ${Math.round(t.compileMs)} ms, debug initialization ${Math.round(t.debugInitMs)} ms, response ${Buffer.byteLength(line, 'utf8')} bytes`);
            }
            m.error
              ? p.reject(
                  Object.assign(new Error(m.error.message), {
                    diagnostic: m.error,
                  }),
                )
              : p.resolve(m.result);
            this.drain();
          }
        } catch (e) {
          log.appendLine(String(e));
        }
      });
    this.process.stderr.on("data", (data) => log.append(data.toString()));
    this.process.on("error", (e) => this.fail(e));
    this.process.on("exit", (code) =>
      this.fail(
        new Error(
          `PixelLang service exited (${code}). Set pixellang.pythonPath to Python 3.11+ with Pillow installed. See PixelLang output.`,
        ),
      ),
    );
  }
  fail(error) {
    this.error = error;
    for (const p of this.pending.values()) { p.cleanup?.(); p.reject(error); }
    this.pending.clear();
    this.active = 0;
  }
  drain() {
    if (this.error) return;
    for (const [id, pending] of this.pending) {
      if (this.active >= 4) break;
      if (pending.sent) continue;
      // The server serializes editor requests on their own compiler. Sending
      // more only occupies RPC threads that a build needs to reach its lane.
      if (editorMethods.has(pending.method) && [...this.pending.values()].some(
        item => item.sent && editorMethods.has(item.method))) continue;
      pending.sent = true;
      pending.sentAt = performance.now();
      this.active++;
      this.process.stdin.write(pending.payload, error => {
        if (!error || this.pending.get(id) !== pending) return;
        this.pending.delete(id);
        this.active--;
        pending.cleanup();
        pending.reject(error);
        this.drain();
      });
    }
  }
  call(method, args = {}, options = {}) {
    if (options.signal?.aborted) return Promise.reject(Object.assign(new Error("Compilation cancelled"), { name: "AbortError" }));
    if (this.error) return Promise.reject(this.error);
    if (this.pending.size >= 64) return Promise.reject(new Error("Studio request backlog is full"));
    return new Promise((resolve, reject) => {
      const id = ++this.next;
      const payload = JSON.stringify(encodeWire({ version: 1, id, operation: "studio", method, args, revision: args.revision })) + "\n";
      const cleanup = () => options.signal?.removeEventListener("abort", abort);
      const pending = { resolve, reject, cleanup, payload, sent: false,
        method, started: performance.now() };
      const abort = () => {
        cleanup();
        if (pending.sent) {
          // Keep its transport slot until the server completes cancellation.
          // Otherwise rapid abort/retry can exceed the server's bounded queue.
          this.process.stdin.write(JSON.stringify({ version: 1, id: ++this.next, operation: "cancel", target: id }) + "\n");
        } else {
          this.pending.delete(id);
          this.drain();
        }
        reject(Object.assign(new Error("Compilation cancelled"), { name: "AbortError" }));
      };
      this.pending.set(id, pending);
      options.signal?.addEventListener("abort", abort, { once: true });
      this.drain();
    });
  }
  dispose() {
    this.process.kill();
    this.fail(new Error("Studio service stopped"));
  }
}
module.exports = { Bridge, encodeWire, decodeWire };
